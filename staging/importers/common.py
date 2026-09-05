from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from pymysql.connections import Connection

from staging.db import fetch_one


@dataclass
class ImportStats:
    rows_total: int = 0
    rows_imported: int = 0
    rows_updated: int = 0
    rows_skipped: int = 0
    rows_errors: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)


def clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    return text


def parse_decimal(value: Any) -> Decimal | None:
    text = clean(value)
    if not text or text.lower() == "nan":
        return None
    text = text.replace(" ", "").replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def content_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bytes_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def close_stale_runs(conn: Connection, supplier_id: int) -> None:
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


def count_supplier_products(conn: Connection, supplier_id: int) -> int:
    row = fetch_one(
        conn,
        "SELECT COUNT(*) AS cnt FROM supplier_products WHERE supplier_id = %s",
        (supplier_id,),
    )
    return int(row["cnt"]) if row else 0


def get_supplier_and_source(
    conn: Connection, supplier_code: str, source_code: str
) -> tuple[int, int | None]:
    supplier = fetch_one(conn, "SELECT id FROM suppliers WHERE code = %s", (supplier_code,))
    if not supplier:
        raise RuntimeError(f"Supplier '{supplier_code}' not found. Run setup_database.py first.")

    source = fetch_one(
        conn,
        """
        SELECT id FROM supplier_sources
        WHERE supplier_id = %s AND code = %s
        """,
        (supplier["id"], source_code),
    )
    return supplier["id"], source["id"] if source else None


def start_import_run(
    conn: Connection,
    supplier_id: int,
    source_id: int | None,
    source_label: str,
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
            (supplier_id, source_id, source_label, source_hash, datetime.now()),
        )
        return int(cur.lastrowid)


def finish_import_run(
    conn: Connection,
    run_id: int,
    stats: ImportStats,
    status: str,
    error_summary: str | None = None,
) -> None:
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


def log_import_error(
    conn: Connection,
    run_id: int,
    row_number: int | None,
    sku: str | None,
    message: str,
    fragment: dict[str, Any] | None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO import_run_errors (
                import_run_id, source_row_number, supplier_sku, error_code, error_message, raw_fragment
            ) VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                run_id,
                row_number,
                sku,
                "parse_or_write",
                message,
                json.dumps(fragment, ensure_ascii=False) if fragment else None,
            ),
        )


def load_existing_hashes(conn: Connection, supplier_id: int, skus: list[str]) -> dict[str, str]:
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


def upsert_product_batch(
    conn: Connection,
    supplier_id: int,
    run_id: int,
    batch: list[dict[str, Any]],
    existing_hashes: dict[str, str],
    stats: ImportStats,
) -> None:
    if not batch:
        return

    insert_sql = """
        INSERT INTO supplier_products (
            supplier_id, import_run_id, supplier_sku, supplier_sku_raw,
            name, brand, manufacturer_code, description, supplier_category, supplier_category_path,
            price, price_retail, price_old, stock_qty, is_available,
            product_url, barcode,
            attributes_json, images_json, raw_data_json, content_hash,
            is_new, is_changed, last_import_run_id, last_seen_at
        ) VALUES (
            %(supplier_id)s, %(import_run_id)s, %(supplier_sku)s, %(supplier_sku_raw)s,
            %(name)s, %(brand)s, %(manufacturer_code)s, %(description)s, %(supplier_category)s, %(supplier_category_path)s,
            %(price)s, %(price_retail)s, %(price_old)s, %(stock_qty)s, %(is_available)s,
            %(product_url)s, %(barcode)s,
            %(attributes_json)s, %(images_json)s, %(raw_data_json)s, %(content_hash)s,
            %(is_new)s, %(is_changed)s, %(import_run_id)s, NOW()
        )
        ON DUPLICATE KEY UPDATE
            import_run_id = VALUES(import_run_id),
            supplier_sku_raw = VALUES(supplier_sku_raw),
            name = VALUES(name),
            brand = VALUES(brand),
            manufacturer_code = VALUES(manufacturer_code),
            -- Never let a source that carries no content erase content another
            -- source provided: the Crystal price XLS has no descriptions or
            -- images, but the site scraper fills both in.
            description = COALESCE(NULLIF(VALUES(description), ''), description),
            supplier_category = VALUES(supplier_category),
            supplier_category_path = VALUES(supplier_category_path),
            price = VALUES(price),
            price_retail = VALUES(price_retail),
            price_old = VALUES(price_old),
            stock_qty = VALUES(stock_qty),
            is_available = VALUES(is_available),
            product_url = VALUES(product_url),
            barcode = VALUES(barcode),
            attributes_json = VALUES(attributes_json),
            images_json = IF(
                JSON_LENGTH(COALESCE(VALUES(images_json), JSON_ARRAY())) > 0,
                VALUES(images_json), images_json
            ),
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
                "supplier_sku_raw": item.get("supplier_sku_raw", sku),
                "name": item.get("name"),
                "brand": item.get("brand"),
                "manufacturer_code": item.get("manufacturer_code"),
                "description": item.get("description"),
                "supplier_category": item.get("supplier_category"),
                "supplier_category_path": item.get("supplier_category_path"),
                "price": item.get("price"),
                "price_retail": item.get("price_retail"),
                "price_old": item.get("price_old"),
                "stock_qty": item.get("stock_qty"),
                "is_available": item.get("is_available"),
                "product_url": item.get("product_url"),
                "barcode": item.get("barcode"),
                "attributes_json": json.dumps(item.get("attributes_json") or {}, ensure_ascii=False),
                "images_json": json.dumps(item.get("images_json") or [], ensure_ascii=False),
                "raw_data_json": json.dumps(item["raw_data_json"], ensure_ascii=False)
                if item.get("raw_data_json") is not None
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
