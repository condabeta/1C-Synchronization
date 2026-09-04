from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterator

import pandas as pd
from pymysql.connections import Connection

from staging.importers.jazzway_feed import JazzwayContent, order_code_for
from staging.importers.common import (
    ImportStats,
    clean,
    close_stale_runs,
    content_hash,
    count_supplier_products,
    file_sha256,
    finish_import_run,
    get_supplier_and_source,
    load_existing_hashes,
    log_import_error,
    parse_decimal,
    start_import_run,
    upsert_product_batch,
)

SUPPLIER_CODE = "jazzway"
SOURCE_CODE = "stock_xlsx"
DEFAULT_XLSX_PATH = r"D:\projects\1C\Джазвея\11.08 Остатки для клиента.xlsx"
HEADER_ROW = 5
BATCH_SIZE = 500

ATTR_COLUMNS = (
    "Уп.",
    "Заказанное количество",
    "Валюта цены",
    "Единица измерения",
    "Количество в пути",
    "Дата ожидаемого прихода",
    "Кратность",
    "Статус",
    "Проектная документация",
    "Цена",
    "Цена.1",
)


def _report(progress: Callable[[str], None] | None, message: str) -> None:
    if progress:
        progress(message)
    else:
        print(message, flush=True)


def _is_product_row(article: Any, price: Any) -> bool:
    sku = clean(article)
    if not sku or parse_decimal(price) is None:
        return False
    if sku.startswith(".") and sku.replace(".", "").replace(" ", "").isdigit() is False:
        if "  " in sku or sku.count(".") > 2:
            return False
    if not sku.startswith(".") and not sku[0].isalnum():
        return False
    return True


def normalize_row(
    row: dict[str, Any],
    *,
    content: JazzwayContent | None = None,
    store_raw: bool = False,
) -> dict[str, Any] | None:
    """Normalize one price-file row, enriched with feed content when available.

    The XLSX owns price and stock; the YML feed owns description, images, specs
    and document links. Neither source alone is enough for a product card.
    """
    if not _is_product_row(row.get("Артикул"), row.get("Цена клиента")):
        return None

    sku = clean(row.get("Артикул"))
    if not sku:
        return None

    price = parse_decimal(row.get("Цена клиента"))
    stock = parse_decimal(row.get("Остаток"))

    images: list[str] = []
    for image_url in [clean(row.get("Ссылка на картинку"))] + (content.images if content else []):
        if image_url and image_url not in images:
            images.append(image_url)

    attrs = {
        key: clean(row.get(key))
        for key in ATTR_COLUMNS
        if clean(row.get(key))
    }
    if content:
        # Price-file columns win on conflict; they are the ones the client sees.
        attrs = {**content.attributes, **attrs}
        if content.documents:
            attrs["documents"] = content.documents

    is_available = 1
    if stock is not None:
        is_available = 1 if stock > 0 else 0

    normalized = {
        "supplier_sku": sku,
        "supplier_sku_raw": sku,
        "name": clean(row.get("Номенклатура")),
        "brand": "Jazzway",
        "manufacturer_code": (content.article if content else None) or sku.lstrip("."),
        "description": content.description if content else None,
        "supplier_category": content.category if content else None,
        "supplier_category_path": content.category_path if content else None,
        "price": price,
        "price_retail": price,
        "price_old": parse_decimal(row.get("Цена.1")) or parse_decimal(row.get("Цена")),
        "stock_qty": stock,
        "is_available": is_available,
        "product_url": clean(row.get("Ссылка на сайт")) or (content.product_url if content else None),
        "barcode": content.barcode if content else None,
        "images_json": images,
        "attributes_json": attrs,
        "raw_data_json": dict(row) if store_raw else None,
    }
    normalized["content_hash"] = content_hash(
        {
            "supplier_sku": sku,
            "name": normalized["name"],
            "description": normalized["description"],
            "price": str(price) if price is not None else None,
            "stock_qty": str(stock) if stock is not None else None,
            "images_json": images,
            "product_url": normalized["product_url"],
            "attributes_json": attrs,
        }
    )
    return normalized


def content_for(
    row: dict[str, Any],
    feed_index: dict[str, JazzwayContent] | None,
) -> JazzwayContent | None:
    if not feed_index:
        return None
    return feed_index.get(order_code_for(clean(row.get("Артикул")) or ""))


def _feed_coverage(df: "pd.DataFrame", feed_index: dict[str, JazzwayContent]) -> tuple[int, int]:
    """(rows matched to the feed, priced rows in the file)."""
    matched = priced = 0
    for _, row in df.iterrows():
        item = row.to_dict()
        if not _is_product_row(item.get("Артикул"), item.get("Цена клиента")):
            continue
        priced += 1
        if content_for(item, feed_index):
            matched += 1
    return matched, priced


def iter_jazzway_rows(
    xlsx_path: Path,
    limit: int | None = None,
    skip_rows: int = 0,
) -> Iterator[dict[str, Any]]:
    df = pd.read_excel(xlsx_path, header=HEADER_ROW)
    emitted = 0
    skipped = 0

    for _, row in df.iterrows():
        item = row.to_dict()
        normalized = normalize_row(item)
        if not normalized:
            continue
        if skipped < skip_rows:
            skipped += 1
            continue
        yield item
        emitted += 1
        if limit is not None and emitted >= limit:
            break


def count_product_rows(xlsx_path: Path) -> int:
    df = pd.read_excel(xlsx_path, header=HEADER_ROW)
    count = 0
    for _, row in df.iterrows():
        if normalize_row(row.to_dict()):
            count += 1
    return count


def import_jazzway_xlsx(
    conn: Connection,
    xlsx_path: Path,
    limit: int | None = None,
    skip_rows: int = 0,
    progress: Callable[[str], None] | None = None,
    store_raw: bool = False,
    feed_index: dict[str, JazzwayContent] | None = None,
) -> ImportStats:
    if not xlsx_path.exists():
        raise FileNotFoundError(f"Jazzway XLSX not found: {xlsx_path}")

    _report(progress, f"Reading Jazzway Excel ({xlsx_path.name})...")
    total_expected = count_product_rows(xlsx_path)
    _report(progress, f"Found ~{total_expected:,} product rows with prices.")

    supplier_id, source_id = get_supplier_and_source(conn, SUPPLIER_CODE, SOURCE_CODE)
    close_stale_runs(conn, supplier_id)

    source_hash = file_sha256(xlsx_path)
    run_id = start_import_run(conn, supplier_id, source_id, str(xlsx_path), source_hash)
    conn.commit()

    stats = ImportStats()
    batch: list[dict[str, Any]] = []
    existing_hashes: dict[str, str] = {}
    batch_number = 0
    product_index = 0

    df = pd.read_excel(xlsx_path, header=HEADER_ROW)

    if feed_index:
        matched, priced = _feed_coverage(df, feed_index)
        _report(
            progress,
            f"Feed matched {matched:,} of {priced:,} priced rows "
            f"({priced - matched:,} will import without description or specs).",
        )
    else:
        _report(progress, "No content feed - importing price and stock only.")

    try:
        for row_number, row in df.iterrows():
            stats.rows_total += 1
            try:
                row_dict = row.to_dict()
                normalized = normalize_row(
                    row_dict,
                    content=content_for(row_dict, feed_index),
                    store_raw=store_raw,
                )
                if not normalized:
                    continue

                product_index += 1
                if product_index <= skip_rows:
                    continue
                if limit is not None and product_index - skip_rows > limit:
                    break

                batch.append(normalized)
                if len(batch) >= BATCH_SIZE:
                    batch_number += 1
                    skus = [item["supplier_sku"] for item in batch]
                    existing_hashes.update(load_existing_hashes(conn, supplier_id, skus))
                    upsert_product_batch(conn, supplier_id, run_id, batch, existing_hashes, stats)
                    conn.commit()
                    batch.clear()
                    _report(
                        progress,
                        (
                            f"  batch {batch_number}: products {product_index:,} | "
                            f"db {count_supplier_products(conn, supplier_id):,} | "
                            f"new {stats.rows_imported:,} | updated {stats.rows_updated:,} | "
                            f"unchanged {stats.rows_skipped:,} | errors {stats.rows_errors:,}"
                        ),
                    )
            except Exception as exc:
                stats.rows_errors += 1
                stats.errors.append({"row": int(row_number) + HEADER_ROW + 2, "error": str(exc)})
                log_import_error(
                    conn,
                    run_id,
                    int(row_number) + HEADER_ROW + 2,
                    clean(row.get("Артикул")),
                    str(exc),
                    row.to_dict(),
                )
                conn.commit()

        if batch:
            batch_number += 1
            skus = [item["supplier_sku"] for item in batch]
            existing_hashes.update(load_existing_hashes(conn, supplier_id, skus))
            upsert_product_batch(conn, supplier_id, run_id, batch, existing_hashes, stats)
            conn.commit()
            _report(
                progress,
                (
                    f"  batch {batch_number}: products {product_index:,} | "
                    f"db {count_supplier_products(conn, supplier_id):,} | "
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

        finish_import_run(conn, run_id, stats, status, error_summary)
        conn.commit()
        _report(progress, "Import completed.")
        return stats
    except Exception:
        conn.rollback()
        finish_import_run(conn, run_id, stats, "failed", "Import aborted due to unexpected error")
        conn.commit()
        raise
