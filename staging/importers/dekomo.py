from __future__ import annotations

import csv
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from html import unescape
from pathlib import Path
from typing import Any, Callable, Iterator

from pymysql.connections import Connection

from staging.db import fetch_one
from staging.importers.common import flush_product_batch
from staging.pricing import apply_to_batch, load_rules

SUPPLIER_CODE = "dekomo"
SOURCE_CODE = "price_csv"
BATCH_SIZE = 1000
PROGRESS_EVERY_BATCHES = 1

CORE_COLUMNS = {
    "Наименование": "name",
    "Артикул": "supplier_sku",
    "МРЦ/РРЦ": "price_retail",
    "Старая РРЦ/МРЦ": "price_old",
    "Количество на складе": "stock_qty",
    "Бренд": "brand",
    "Артикул поставщика": "manufacturer_code",
    "Группа товара": "supplier_category",
    "Дополнительное описание": "description",
    # Present from the 09.09.2026 export onwards, which widened from 26 columns
    # to 170. Absent from older files, so both are read defensively.
    "Штрих-код": "barcode",
    "ТН ВЭД": "tnved",
    "Закупочная цена": "price",
}

IMAGE_COLUMNS = ("photo_big", "photo_small", "Большое дополнительное фото")


@dataclass
class ImportStats:
    rows_total: int = 0
    rows_imported: int = 0
    rows_updated: int = 0
    rows_skipped: int = 0
    rows_errors: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_decimal(value: Any) -> Decimal | None:
    text = _clean(value)
    if not text:
        return None
    text = text.replace(" ", "").replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _parse_stock(value: Any) -> Decimal | None:
    text = _clean(value)
    if not text:
        return None
    text = text.replace(" ", "").replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _build_images(row: dict[str, Any]) -> list[str]:
    images: list[str] = []
    seen: set[str] = set()
    for column in IMAGE_COLUMNS:
        url = _clean(row.get(column))
        if url and url not in seen:
            seen.add(url)
            images.append(url)
    return images


def _build_attributes(row: dict[str, Any], core_and_image: set[str]) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    for key, value in row.items():
        if key in core_and_image:
            continue
        cleaned = _clean(value)
        if cleaned:
            attrs[key] = cleaned
    return attrs


BARCODE_MAX = 64  # supplier_products.barcode is VARCHAR(64)


def _split_barcodes(value: Any) -> list[str]:
    """Individual GTINs from a cell that may hold several, comma-separated."""
    text = _clean(value)
    if not text:
        return []
    parts = [part.strip() for part in re.split(r"[,;\s]+", text)]
    return [part[:BARCODE_MAX] for part in parts if part]


def _content_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _normalize_row(row: dict[str, Any], *, store_raw: bool) -> dict[str, Any] | None:
    sku = _clean(row.get("Артикул"))
    if not sku:
        return None

    price_retail = _parse_decimal(row.get("МРЦ/РРЦ"))
    price_old = _parse_decimal(row.get("Старая РРЦ/МРЦ"))
    # The 09.09.2026 export added a real purchase price. Before it, `price` had
    # to stand in as a copy of the MRC. Keeping the MRC as `price_retail` means
    # no displayed price moves - this only makes `price` mean for Dekomo what it
    # already means for every other supplier, and gives a markup rule something
    # to multiply if the client ever asks for one.
    price_purchase = _parse_decimal(row.get("Закупочная цена"))
    stock_qty = _parse_stock(row.get("Количество на складе"))
    images = _build_images(row)
    attrs = _build_attributes(row, set(CORE_COLUMNS) | set(IMAGE_COLUMNS))

    # 72 products carry several GTINs in one cell - one per pack or variant -
    # comma-separated, up to 133 characters. The column holds a single GTIN for
    # matching across suppliers, so it takes the first; the full list is kept.
    barcodes = _split_barcodes(row.get("Штрих-код"))
    barcode = barcodes[0] if barcodes else None
    if len(barcodes) > 1:
        attrs["barcodes"] = barcodes
    # Stored under the key Arlight already uses, so one query can read the
    # customs code across suppliers instead of guessing at each one's spelling.
    tnved = _clean(row.get("ТН ВЭД"))
    if tnved:
        attrs["tnved"] = tnved

    normalized = {
        "supplier_sku": sku,
        "supplier_sku_raw": sku,
        "name": _clean(row.get("Наименование")),
        "brand": _clean(row.get("Бренд")),
        "manufacturer_code": _clean(row.get("Артикул поставщика")),
        "supplier_category": _clean(row.get("Группа товара")),
        "description": _clean(row.get("Дополнительное описание")),
        "price": price_purchase or price_retail,
        "price_retail": price_retail,
        "price_old": price_old,
        "stock_qty": stock_qty,
        "is_available": 1 if stock_qty is None or stock_qty > 0 else 0,
        "barcode": barcode,
        "images_json": images,
        "attributes_json": attrs,
        "raw_data_json": row if store_raw else None,
    }
    normalized["content_hash"] = _content_hash(
        {
            "supplier_sku": normalized["supplier_sku"],
            "name": normalized["name"],
            "brand": normalized["brand"],
            "description": normalized["description"],
            "price": str(price_purchase) if price_purchase is not None else None,
            "price_retail": str(price_retail) if price_retail is not None else None,
            "price_old": str(price_old) if price_old is not None else None,
            "stock_qty": str(stock_qty) if stock_qty is not None else None,
            "barcode": barcode,
            "images_json": images,
            "attributes_json": attrs,
        }
    )
    return normalized


_TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_ROW_END_RE = re.compile(r"</tr\s*>", re.IGNORECASE)
READ_CHUNK = 1 << 20


def _cell_text(fragment: str) -> str:
    """Text of one cell, matching what BeautifulSoup's get_text(strip=True) gave."""
    return unescape(_TAG_RE.sub("", fragment)).strip()


def _iter_html_rows(csv_path: Path) -> Iterator[dict[str, Any]]:
    """Stream the HTML export one <tr> at a time.

    The 09.09.2026 file is 544 MB: 170 columns across ~200k rows, roughly 34
    million cells. Parsing that into a DOM needs an object per cell and runs the
    machine out of memory long before the first row is written, so rows are cut
    out of a sliding buffer and dropped as soon as they are yielded.
    """
    headers: list[str] | None = None
    buffer = ""

    with csv_path.open("r", encoding="utf-8-sig", errors="replace") as handle:
        while True:
            chunk = handle.read(READ_CHUNK)
            if not chunk:
                break
            buffer += chunk
            while True:
                match = _ROW_END_RE.search(buffer)
                if not match:
                    break
                block, buffer = buffer[: match.start()], buffer[match.end() :]
                cells = [_cell_text(cell) for cell in _TD_RE.findall(block)]
                if not cells:
                    continue
                if headers is None:
                    headers = cells
                    continue
                yield dict(zip(headers, cells))

    # A final row is not always closed with </tr> before </table>.
    if headers is not None and buffer:
        cells = [_cell_text(cell) for cell in _TD_RE.findall(buffer)]
        if cells:
            yield dict(zip(headers, cells))


def _iter_csv_rows(csv_path: Path) -> Iterator[dict[str, Any]]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle, delimiter=";")


def iter_dekomo_rows(
    csv_path: Path,
    limit: int | None = None,
    skip_rows: int = 0,
) -> Iterator[dict[str, Any]]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        head = handle.read(10)
    is_html = "<!DOCTYPE" in head or "<html" in head

    source = _iter_html_rows(csv_path) if is_html else _iter_csv_rows(csv_path)

    for index, row in enumerate(source, start=1):
        if index <= skip_rows:
            continue
        if limit is not None and index - skip_rows > limit:
            break
        yield row


def _close_stale_runs(conn: Connection, supplier_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE import_runs
            SET status = 'failed',
                finished_at = NOW(),
                error_summary = 'Interrupted or replaced by a newer import run'
            WHERE supplier_id = %s AND status = 'running'
            """,
            (supplier_id,),
        )


def _count_supplier_products(conn: Connection, supplier_id: int) -> int:
    row = fetch_one(
        conn,
        "SELECT COUNT(*) AS cnt FROM supplier_products WHERE supplier_id = %s",
        (supplier_id,),
    )
    return int(row["cnt"]) if row else 0


def _get_supplier_and_source(conn: Connection) -> tuple[int, int | None]:
    supplier = fetch_one(conn, "SELECT id FROM suppliers WHERE code = %s", (SUPPLIER_CODE,))
    if not supplier:
        raise RuntimeError(f"Supplier '{SUPPLIER_CODE}' not found. Run setup_database.py first.")

    source = fetch_one(
        conn,
        """
        SELECT id FROM supplier_sources
        WHERE supplier_id = %s AND code = %s
        """,
        (supplier["id"], SOURCE_CODE),
    )
    return supplier["id"], source["id"] if source else None


def _start_import_run(
    conn: Connection,
    supplier_id: int,
    source_id: int | None,
    csv_path: Path,
    source_hash: str,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO import_runs (
                supplier_id, source_id, status, trigger_type,
                source_file, source_hash, started_at
            ) VALUES (%s, %s, 'running', 'manual', %s, %s, %s)
            """,
            (supplier_id, source_id, str(csv_path), source_hash, datetime.now()),
        )
        return int(cur.lastrowid)


def _finish_import_run(conn: Connection, run_id: int, stats: ImportStats, status: str, error_summary: str | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE import_runs
            SET status = %s,
                rows_total = %s,
                rows_imported = %s,
                rows_updated = %s,
                rows_skipped = %s,
                rows_errors = %s,
                finished_at = %s,
                error_summary = %s
            WHERE id = %s
            """,
            (
                status,
                stats.rows_total,
                stats.rows_imported,
                stats.rows_updated,
                stats.rows_skipped,
                stats.rows_errors,
                datetime.now(),
                error_summary,
                run_id,
            ),
        )


def _log_error(conn: Connection, run_id: int, row_number: int, sku: str | None, message: str, fragment: dict[str, Any] | None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO import_run_errors (
                import_run_id, source_row_number, supplier_sku, error_code, error_message, raw_fragment
            ) VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (run_id, row_number, sku, "parse_or_write", message, json.dumps(fragment, ensure_ascii=False) if fragment else None),
        )


def _load_existing_hashes(conn: Connection, supplier_id: int, skus: list[str]) -> dict[str, str]:
    if not skus:
        return {}
    placeholders = ", ".join(["%s"] * len(skus))
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT supplier_sku, content_hash
            FROM supplier_products
            WHERE supplier_id = %s AND supplier_sku IN ({placeholders})
            """,
            [supplier_id, *skus],
        )
        return {row["supplier_sku"]: row["content_hash"] for row in cur.fetchall()}


def _upsert_batch(
    conn: Connection,
    supplier_id: int,
    run_id: int,
    batch: list[dict[str, Any]],
    existing_hashes: dict[str, str],
    stats: ImportStats,
) -> None:
    if not batch:
        return

    # Dekomo ships an MRC/RRC column, so it has no markup rules by default and
    # this leaves the price untouched. It runs anyway so that adding a rule for
    # Dekomo later needs no code change here.
    apply_to_batch(load_rules(conn, supplier_id), batch)

    insert_sql = """
        INSERT INTO supplier_products (
            supplier_id, import_run_id, supplier_sku, supplier_sku_raw,
            name, brand, manufacturer_code, supplier_category, description,
            price, price_retail, price_old, stock_qty, is_available,
            barcode, pricing_rule_id,
            attributes_json, images_json, raw_data_json, content_hash,
            is_new, is_changed, last_import_run_id, last_seen_at
        ) VALUES (
            %(supplier_id)s, %(import_run_id)s, %(supplier_sku)s, %(supplier_sku_raw)s,
            %(name)s, %(brand)s, %(manufacturer_code)s, %(supplier_category)s, %(description)s,
            %(price)s, %(price_retail)s, %(price_old)s, %(stock_qty)s, %(is_available)s,
            %(barcode)s, %(pricing_rule_id)s,
            %(attributes_json)s, %(images_json)s, %(raw_data_json)s, %(content_hash)s,
            %(is_new)s, %(is_changed)s, %(import_run_id)s, NOW()
        )
        ON DUPLICATE KEY UPDATE
            import_run_id = VALUES(import_run_id),
            supplier_sku_raw = VALUES(supplier_sku_raw),
            name = VALUES(name),
            brand = VALUES(brand),
            manufacturer_code = VALUES(manufacturer_code),
            supplier_category = VALUES(supplier_category),
            description = VALUES(description),
            price = VALUES(price),
            price_retail = VALUES(price_retail),
            price_old = VALUES(price_old),
            stock_qty = VALUES(stock_qty),
            is_available = VALUES(is_available),
            -- An older export has no barcode column at all, so a re-import from
            -- one must not wipe a code a newer file already supplied.
            barcode = COALESCE(VALUES(barcode), barcode),
            pricing_rule_id = VALUES(pricing_rule_id),
            attributes_json = VALUES(attributes_json),
            images_json = VALUES(images_json),
            raw_data_json = VALUES(raw_data_json),
            content_hash = VALUES(content_hash),
            is_new = 0,
            is_changed = VALUES(is_changed),
            last_import_run_id = VALUES(last_import_run_id),
            last_seen_at = NOW()
    """

    rows_to_write: list[dict[str, Any]] = []
    unchanged_skus: list[str] = []

    for item in batch:
        sku = item["supplier_sku"]
        old_hash = existing_hashes.get(sku)
        is_new = old_hash is None
        is_changed = bool(old_hash and old_hash != item["content_hash"])

        if is_new:
            stats.rows_imported += 1
        elif is_changed:
            stats.rows_updated += 1
        else:
            stats.rows_skipped += 1
            unchanged_skus.append(sku)
            existing_hashes[sku] = item["content_hash"]
            continue

        rows_to_write.append(
            {
                "supplier_id": supplier_id,
                "import_run_id": run_id,
                "supplier_sku": sku,
                "supplier_sku_raw": item["supplier_sku_raw"],
                "name": item["name"],
                "brand": item["brand"],
                "manufacturer_code": item["manufacturer_code"],
                "supplier_category": item["supplier_category"],
                "description": item["description"],
                "price": item["price"],
                "price_retail": item["price_retail"],
                "price_old": item["price_old"],
                "stock_qty": item["stock_qty"],
                "is_available": item["is_available"],
                "barcode": item.get("barcode"),
                "pricing_rule_id": item.get("pricing_rule_id"),
                "attributes_json": json.dumps(item["attributes_json"], ensure_ascii=False),
                "images_json": json.dumps(item["images_json"], ensure_ascii=False),
                "raw_data_json": json.dumps(item["raw_data_json"], ensure_ascii=False)
                if item["raw_data_json"] is not None
                else None,
                "content_hash": item["content_hash"],
                "is_new": 1 if is_new else 0,
                "is_changed": 1 if is_changed else 0,
            }
        )
        existing_hashes[sku] = item["content_hash"]

    with conn.cursor() as cur:
        if rows_to_write:
            cur.executemany(insert_sql, rows_to_write)
        if unchanged_skus:
            placeholders = ", ".join(["%s"] * len(unchanged_skus))
            cur.execute(
                f"""
                UPDATE supplier_products
                SET last_seen_at = NOW(), last_import_run_id = %s
                WHERE supplier_id = %s AND supplier_sku IN ({placeholders})
                """,
                [run_id, supplier_id, *unchanged_skus],
            )


def import_dekomo_csv(
    conn: Connection,
    csv_path: Path,
    limit: int | None = None,
    skip_rows: int = 0,
    progress: Callable[[str], None] | None = None,
    store_raw: bool = False,
) -> ImportStats:
    if not csv_path.exists():
        raise FileNotFoundError(f"Dekomo CSV not found: {csv_path}")

    _report(progress, f"Reading supplier config...")
    supplier_id, source_id = _get_supplier_and_source(conn)
    _close_stale_runs(conn, supplier_id)

    db_count_start = _count_supplier_products(conn, supplier_id)
    if db_count_start:
        _report(
            progress,
            f"Resuming: {db_count_start:,} products already in DB. "
            "Existing rows are skipped quickly; new rows appear after ~row 43,000.",
        )

    _report(progress, f"Hashing CSV file ({csv_path.name})...")
    source_hash = file_sha256(csv_path)
    run_id = _start_import_run(conn, supplier_id, source_id, csv_path, source_hash)
    conn.commit()

    stats = ImportStats()
    batch: list[dict[str, Any]] = []
    existing_hashes: dict[str, str] = {}
    batch_number = 0

    _report(progress, "Import started. Progress updates appear after each batch.")

    try:
        for row_number, row in enumerate(
            iter_dekomo_rows(csv_path, limit=limit, skip_rows=skip_rows),
            start=2 + skip_rows,
        ):
            stats.rows_total += 1
            try:
                normalized = _normalize_row(row, store_raw=store_raw)
            except Exception as exc:
                stats.rows_errors += 1
                stats.errors.append({"row": row_number, "error": str(exc)})
                _log_error(conn, run_id, row_number, row.get("Артикул"), str(exc), row)
                conn.commit()
                continue
            if not normalized:
                stats.rows_skipped += 1
                continue
            batch.append(normalized)

            if len(batch) >= BATCH_SIZE:
                batch_number += 1
                flush_product_batch(
                    conn, supplier_id, run_id, batch, existing_hashes, stats,
                    upsert=_upsert_batch, load_hashes=_load_existing_hashes, log_error=_log_error,
                )

                if batch_number % PROGRESS_EVERY_BATCHES == 0:
                    db_count = _count_supplier_products(conn, supplier_id)
                    _report(
                        progress,
                        (
                            f"  batch {batch_number}: csv {stats.rows_total:,} rows | "
                            f"db {db_count:,} products | "
                            f"new {stats.rows_imported:,} | updated {stats.rows_updated:,} | "
                            f"unchanged {stats.rows_skipped:,} | errors {stats.rows_errors:,}"
                        ),
                    )

        if batch:
            batch_number += 1
            flush_product_batch(
                conn, supplier_id, run_id, batch, existing_hashes, stats,
                upsert=_upsert_batch, load_hashes=_load_existing_hashes, log_error=_log_error,
            )
            _report(
                progress,
                (
                    f"  batch {batch_number}: csv {stats.rows_total:,} rows | "
                    f"db {_count_supplier_products(conn, supplier_id):,} products | "
                    f"new {stats.rows_imported:,} | updated {stats.rows_updated:,} | "
                    f"unchanged {stats.rows_skipped:,} | errors {stats.rows_errors:,}"
                ),
            )

        status = "success"
        if stats.rows_errors and (stats.rows_imported or stats.rows_updated):
            status = "partial"
        elif stats.rows_errors and not (stats.rows_imported or stats.rows_updated):
            status = "failed"

        error_summary = None
        if stats.errors:
            error_summary = "; ".join(
                f"row {item['row']}: {item['error']}" for item in stats.errors[:5]
            )

        _finish_import_run(conn, run_id, stats, status, error_summary)
        conn.commit()
        _report(progress, "Import completed.")
        return stats
    except Exception:
        conn.rollback()
        _finish_import_run(
            conn,
            run_id,
            stats,
            "failed",
            "Import aborted due to unexpected error",
        )
        conn.commit()
        raise


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _report(progress: Callable[[str], None] | None, message: str) -> None:
    if progress:
        progress(message)
    else:
        print(message, flush=True)
