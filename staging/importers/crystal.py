from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable, Iterator

import xlrd
from pymysql.connections import Connection

from staging.importers.common import (
    ImportStats,
    clean,
    close_stale_runs,
    content_hash,
    count_supplier_products,
    file_sha256,
    finish_import_run,
    get_supplier_and_source,
    log_import_error,
    parse_decimal,
    start_import_run,
    flush_product_batch,
)
from staging.importers.crystal_site import SiteProductInfo, fetch_product_info

SUPPLIER_CODE = "crystal"
SOURCE_CODE = "price_xls"
DEFAULT_XLS_PATH = r"D:\projects\1C\crystal\ПРАЙС LEDCRYSTAL от 05.08.2026.xls"
BATCH_SIZE = 200
PRICE_HEADERS = ("Цена", "Цена/м", "Цена/шт", "Цена/компл")
SKIP_HEADER_VALUES = {"Артикул", "Фото", "Наименование", "Наименование товара"}
SKU_RE = re.compile(r"^[A-ZА-Я0-9][A-ZА-Я0-9.\-/]*$", re.I)


def _report(progress: Callable[[str], None] | None, message: str) -> None:
    if progress:
        progress(message)
    else:
        print(message, flush=True)


def _row_values(sheet: xlrd.sheet.Sheet, row_index: int) -> list[Any]:
    return [sheet.cell_value(row_index, col_index) for col_index in range(sheet.ncols)]


def _is_header_row(values: list[Any]) -> dict[str, int] | None:
    headers: dict[str, int] = {}
    for index, value in enumerate(values):
        header = clean(value)
        if header:
            headers[header] = index
    if "Артикул" not in headers:
        return None
    return headers


def _pick_price(values: list[Any], headers: dict[str, int]) -> Any:
    for header in PRICE_HEADERS:
        index = headers.get(header)
        if index is None:
            continue
        value = values[index] if index < len(values) else None
        if parse_decimal(value) is not None:
            return value
    return None


def _looks_like_product_row(sku: str | None, price: Any) -> bool:
    if not sku or sku in SKIP_HEADER_VALUES:
        return False
    if not SKU_RE.match(sku.replace(" ", "")):
        return False
    if parse_decimal(price) is None:
        return False
    return True


def _build_name(
    sku: str,
    values: list[Any],
    headers: dict[str, int],
    sheet_name: str,
) -> str:
    for header in ("Наименование", "Наименование товара"):
        index = headers.get(header)
        if index is not None:
            name = clean(values[index])
            if name:
                return name

    parts = [sku]
    for header in ("Тип диодов", "Мощность", "Напряжение", "Цвет", "Цоколь", "Мощность, W"):
        index = headers.get(header)
        if index is None:
            continue
        value = clean(values[index])
        if value:
            parts.append(value)
    if len(parts) > 1:
        return ", ".join(parts)

    return f"{sheet_name.strip()} {sku}"


def _build_attributes(
    values: list[Any],
    headers: dict[str, int],
    sheet_name: str,
) -> dict[str, Any]:
    attrs = {"sheet": sheet_name.strip()}
    skip = {"Фото", "Артикул", "Наименование", "Наименование товара", *PRICE_HEADERS}
    for header, index in headers.items():
        if header in skip:
            continue
        value = clean(values[index] if index < len(values) else None)
        if value:
            attrs[header] = value
    return attrs


def normalize_row(
    *,
    sheet_name: str,
    row_number: int,
    values: list[Any],
    headers: dict[str, int],
    site_info: SiteProductInfo | None = None,
    store_raw: bool = False,
) -> dict[str, Any] | None:
    sku_index = headers["Артикул"]
    sku_raw = clean(values[sku_index] if sku_index < len(values) else None)
    if not sku_raw:
        return None
    sku = sku_raw.replace(" ", "")
    price = _pick_price(values, headers)
    if not _looks_like_product_row(sku, price):
        return None

    parsed_price = parse_decimal(price)
    if parsed_price is None:
        return None

    name = _build_name(sku, values, headers, sheet_name)
    attrs = _build_attributes(values, headers, sheet_name)
    images = list(site_info.images) if site_info and site_info.images else []
    product_url = site_info.product_url if site_info and site_info.product_url else "https://led-crystal.ru"
    description = site_info.description if site_info and site_info.description else ""
    if site_info and site_info.description:
        attrs["description"] = site_info.description

    normalized = {
        "supplier_sku": sku,
        "supplier_sku_raw": sku_raw,
        "name": name,
        "brand": "LED Crystal",
        "manufacturer_code": sku,
        "description": description,
        "supplier_category": sheet_name.strip(),
        "supplier_category_path": sheet_name.strip(),
        "price": parsed_price,
        "price_retail": parsed_price,
        "price_old": None,
        "stock_qty": None,
        "is_available": 1,
        "product_url": product_url,
        "barcode": None,
        "images_json": images,
        "attributes_json": attrs,
        "raw_data_json": {
            "sheet": sheet_name,
            "row_number": row_number,
            "values": [clean(v) for v in values],
        }
        if store_raw
        else None,
        "source_row_number": row_number,
    }
    normalized["content_hash"] = content_hash(
        {
            "supplier_sku": sku,
            "name": name,
            "description": description,
            "price": str(parsed_price),
            "images_json": images,
            "product_url": product_url,
            "attributes_json": attrs,
        }
    )
    return normalized


def iter_crystal_rows(
    xlsx_path: Path,
    *,
    fetch_images: bool = False,
    image_limit: int | None = None,
    progress: Callable[[str], None] | None = None,
    store_raw: bool = False,
) -> Iterator[dict[str, Any]]:
    workbook = xlrd.open_workbook(str(xlsx_path))
    fetched_images = 0

    for sheet_name in workbook.sheet_names():
        sheet = workbook.sheet_by_name(sheet_name)
        headers: dict[str, int] | None = None

        for row_index in range(sheet.nrows):
            values = _row_values(sheet, row_index)
            maybe_header = _is_header_row(values)
            if maybe_header:
                headers = maybe_header
                continue
            if not headers:
                continue

            site_info = None
            sku_index = headers["Артикул"]
            sku_raw = clean(values[sku_index] if sku_index < len(values) else None)
            price = _pick_price(values, headers)
            if not _looks_like_product_row(sku_raw.replace(" ", "") if sku_raw else None, price):
                continue

            if fetch_images and (image_limit is None or fetched_images < image_limit):
                site_info = fetch_product_info(
                    sku_raw.replace(" ", ""),
                    progress=progress,
                )
                if site_info.images:
                    fetched_images += 1

            item = normalize_row(
                sheet_name=sheet_name,
                row_number=row_index + 1,
                values=values,
                headers=headers,
                site_info=site_info,
                store_raw=store_raw,
            )
            if item:
                yield item


def count_crystal_products(xlsx_path: Path) -> int:
    return sum(1 for _ in iter_crystal_rows(xlsx_path))


def import_crystal_xls(
    conn: Connection,
    xlsx_path: Path,
    limit: int | None = None,
    skip_rows: int = 0,
    fetch_images: bool = False,
    image_limit: int | None = None,
    progress: Callable[[str], None] | None = None,
    store_raw: bool = False,
) -> ImportStats:
    if not xlsx_path.exists():
        raise FileNotFoundError(f"Crystal XLS not found: {xlsx_path}")

    supplier_id, source_id = get_supplier_and_source(conn, SUPPLIER_CODE, SOURCE_CODE)
    _report(progress, f"Reading Crystal Excel ({xlsx_path.name})...")
    total_expected = count_crystal_products(xlsx_path)
    _report(progress, f"Found ~{total_expected:,} product rows.")

    close_stale_runs(conn, supplier_id)
    source_hash = file_sha256(xlsx_path)
    run_id = start_import_run(conn, supplier_id, source_id, str(xlsx_path), source_hash)
    conn.commit()

    stats = ImportStats()
    batch: list[dict[str, Any]] = []
    existing_hashes: dict[str, str] = {}
    batch_number = 0
    product_index = 0

    try:
        for item in iter_crystal_rows(
            xlsx_path,
            fetch_images=fetch_images,
            image_limit=image_limit,
            progress=progress,
            store_raw=store_raw,
        ):
            stats.rows_total += 1
            try:
                product_index += 1
                if product_index <= skip_rows:
                    continue
                if limit is not None and product_index - skip_rows > limit:
                    break

                batch.append(item)
                if len(batch) >= BATCH_SIZE:
                    batch_number += 1
                    flush_product_batch(conn, supplier_id, run_id, batch, existing_hashes, stats)
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
                stats.errors.append(
                    {
                        "row": item.get("source_row_number"),
                        "sku": item.get("supplier_sku"),
                        "error": str(exc),
                    }
                )
                log_import_error(
                    conn,
                    run_id,
                    item.get("source_row_number"),
                    item.get("supplier_sku"),
                    str(exc),
                    item,
                )
                conn.commit()

        if batch:
            batch_number += 1
            flush_product_batch(conn, supplier_id, run_id, batch, existing_hashes, stats)
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
                f"{item.get('sku', '?')}: {item['error']}" for item in stats.errors[:5]
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
