from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pandas as pd

from pymysql.connections import Connection

from staging.config import ARLIGHT_XLSX_DEFAULT, ARLIGHT_XML_DEFAULT
from staging.importers.common import (
    ImportStats,
    bytes_sha256,
    clean,
    close_stale_runs,
    content_hash,
    count_supplier_products,
    finish_import_run,
    get_supplier_and_source,
    log_import_error,
    parse_decimal,
    start_import_run,
    flush_product_batch,
)

SUPPLIER_CODE = "arlight"
SOURCE_CODE = "xml_excel"
DEFAULT_XML_PATH = ARLIGHT_XML_DEFAULT
DEFAULT_EXCEL_PATH = ARLIGHT_XLSX_DEFAULT
BATCH_SIZE = 500


def _report(progress, message: str) -> None:
    if progress:
        progress(message)
    else:
        print(message, flush=True)


# The price file is a stack of blocks. Each one opens with a section line, then
# repeats the column header, then lists its products:
#
#   Светодиодные ленты | ... | COB сплошное свечение / X360 5V 8mm 5 W/m
#                      | ... | Бескорпусные ленты, серия X360.      <- a note
#   Фото | Артикул | Склад | Наименование | Страна | Цена (руб.) | ...
#        | 041701  | 0     | Лента COB-X360-8mm ...
#
# Column A holds the department, column F the section within it, already written
# as a path. That is the only place Arlight states a category: the XML carries
# numeric group ids with no dictionary to resolve them.
CATEGORY_COLUMN = 0
SECTION_COLUMN = 5
SKU_COLUMN = 1


def parse_excel_price(excel_path: str) -> dict[str, dict[str, Any]]:
    """Parse the Excel price file and return a dict by SKU, section included."""
    _report(None, f"Parsing Excel price file: {excel_path}")
    frame = pd.read_excel(excel_path, header=None)

    def cell(row: "pd.Series", index: int) -> str:
        if index >= len(row):
            return ""
        value = row.iloc[index]
        return "" if pd.isna(value) else str(value).strip()

    headers: dict[str, int] | None = None
    department = ""
    section = ""
    price_data: dict[str, dict[str, Any]] = {}

    for _, row in frame.iterrows():
        first = cell(row, SKU_COLUMN)

        if first == "Артикул":  # the header repeats above every block
            headers = {cell(row, i): i for i in range(len(row)) if cell(row, i)}
            continue

        if not first:
            # A line with no article is either a section line or a note under
            # one. Only a line that names a department opens a new section; the
            # note that may follow it leaves the section alone.
            top, sub = cell(row, CATEGORY_COLUMN), cell(row, SECTION_COLUMN)
            if top:
                department, section = top, sub
            continue

        if headers is None:
            continue  # data before any header: not a price row

        def value(column: str) -> Any:
            index = headers.get(column)
            return None if index is None else row.iloc[index]

        path = " / ".join(part for part in (department, section) if part)
        # The section is itself a path ("COB сплошное свечение / X360 5V 8mm"),
        # so the category is its last step. The separator is a spaced slash: a
        # bare one also appears inside a step, as in "5 W/m".
        leaf = section.split(" / ")[-1].strip() if section else department
        price_data[first] = {
            "stock_qty": parse_decimal(value("Склад")),
            "price": parse_decimal(value("Цена (руб.)")),
            "country": clean(value("Страна")),
            "excel_description": clean(value("Описание")),
            "supplier_category": leaf or None,
            "supplier_category_path": path or None,
        }

    with_section = sum(1 for item in price_data.values() if item["supplier_category"])
    _report(None, f"Parsed {len(price_data)} SKUs from Excel, {with_section} with a section")
    return price_data


# "Лента COB-X360-8mm 5V White6000 (Arlight, IP20 2-5м)" - Arlight writes the
# brand at the head of the trailing parentheses. The XML's <brand> is a numeric
# id (18,138 of 19,451 products are brand "4") and the feed ships no dictionary
# for it, so every card would have shown "4" as its manufacturer.
# The brand is the first word of a parenthesised group, followed by a comma:
# "(Arlight, IP20 Металл, 3 года)". Requiring that comma is what separates the
# brand from a nested aside - "(Arlight, 5мм (цилиндр))" was read as "цилиндр"
# until it was added, and 18,138 products were branded that way.
BRAND_IN_NAME_RE = re.compile(r"\(\s*([^(),]{2,40}?)\s*,")


def brand_from_name(name: str | None) -> str | None:
    matches = BRAND_IN_NAME_RE.findall(name or "")
    for candidate in reversed(matches):  # the brand group is the last one
        text = candidate.strip()
        if len(text) >= 3 and not text.isdigit():
            return text
    return None


def parse_xml_products(xml_path: str) -> list[dict[str, Any]]:
    """Parse XML products file."""
    _report(None, f"Parsing XML products file: {xml_path}")
    tree = ET.parse(xml_path)
    root = tree.getroot()
    
    products = []
    for product_elem in root.findall(".//product"):
        article = clean(product_elem.findtext("article"))
        if not article:
            continue
        
        # Extract description from texts
        description = ""
        application = ""
        for text_elem in product_elem.findall("texts/text"):
            text_type = text_elem.get("type")
            if text_type == "descript":
                description = clean(text_elem.text)
            elif text_type == "application":
                application = clean(text_elem.text)
        
        # Extract images from files
        images = []
        for file_elem in product_elem.findall("files/file"):
            file_type = file_elem.get("type")
            if file_type in ("photo", "img.main", "photo-main", "image"):
                url = clean(file_elem.text)
                if url:
                    images.append(url)
        
        product = {
            "supplier_sku": article,
            "supplier_sku_raw": article,
            "name": clean(product_elem.findtext("name")),
            "brand": brand_from_name(clean(product_elem.findtext("name"))),
            "manufacturer_code": article,
            "ean13": clean(product_elem.findtext("ean13")),
            "warranty": clean(product_elem.findtext("warranty")),
            "description": description,
            "application": application,
            "country": clean(product_elem.findtext("country")),
            "images_json": images,
            "tnved": clean(product_elem.findtext("tnved")),
            "tnvedtext": clean(product_elem.findtext("tnvedtext")),
        }
        products.append(product)
    
    _report(None, f"Parsed {len(products)} products from XML")
    return products


def merge_arlight_data(
    xml_products: list[dict[str, Any]],
    excel_data: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge XML product data with Excel pricing/stock data."""
    merged = []
    for product in xml_products:
        sku = product["supplier_sku"]
        price_info = excel_data.get(sku, {})
        
        # Use Excel description if XML description is missing
        description = product["description"] or price_info.get("excel_description", "")
        
        merged_product = {
            "supplier_sku": sku,
            "supplier_sku_raw": sku,
            "name": product["name"],
            "brand": product["brand"],
            "manufacturer_code": product["manufacturer_code"],
            "description": description,
            # From the price file's section lines. The XML's <groups> are
            # numeric ids and the feed ships no dictionary for them.
            "supplier_category": price_info.get("supplier_category"),
            "supplier_category_path": price_info.get("supplier_category_path"),
            "price": price_info.get("price"),
            "price_retail": price_info.get("price"),
            "price_old": None,
            "stock_qty": price_info.get("stock_qty"),
            # A SKU absent from the price file, or present with an empty stock
            # cell, counts as unavailable - comparing None here used to raise.
            "is_available": 1 if (price_info.get("stock_qty") or 0) > 0 else 0,
            "product_url": None,
            "barcode": product["ean13"],
            "country": price_info.get("country") or product["country"],
            "images_json": product["images_json"],
            "attributes_json": {
                "tnved": product["tnved"],
                "tnvedtext": product["tnvedtext"],
                "warranty": product["warranty"],
                "ean13": product["ean13"],
            },
            "raw_data_json": None,
        }
        
        merged_product["content_hash"] = content_hash(
            {
                "supplier_sku": sku,
                "name": merged_product["name"],
                "brand": merged_product["brand"],
                "description": merged_product["description"],
                # The price file is also the stock source, and a stock move is
                # often the only change in a row. Leaving stock_qty and the
                # section out of the hash made such a row look unchanged, so the
                # upsert skipped it and the DB kept yesterday's stock.
                "price": str(merged_product["price"]) if merged_product["price"] is not None else None,
                "stock_qty": (
                    str(merged_product["stock_qty"])
                    if merged_product["stock_qty"] is not None else None
                ),
                "supplier_category_path": merged_product["supplier_category_path"],
                "is_available": merged_product["is_available"],
                "images_json": merged_product["images_json"],
                "attributes_json": merged_product["attributes_json"],
            }
        )
        
        merged.append(merged_product)
    
    return merged


def import_arlight(
    conn: Connection,
    xml_path: str = DEFAULT_XML_PATH,
    excel_path: str = DEFAULT_EXCEL_PATH,
    limit: int | None = None,
    progress = None,
    store_raw: bool = False,
) -> ImportStats:
    _report(progress, "Starting Arlight import...")
    
    # Parse both files
    xml_products = parse_xml_products(xml_path)
    excel_data = parse_excel_price(excel_path)
    
    # Merge data
    merged_products = merge_arlight_data(xml_products, excel_data)
    
    if limit:
        merged_products = merged_products[:limit]
    
    _report(progress, f"Merged {len(merged_products)} products")
    
    # Calculate source hash
    xml_hash = bytes_sha256(Path(xml_path).read_bytes())
    excel_hash = bytes_sha256(Path(excel_path).read_bytes())
    source_hash = bytes_sha256(f"{xml_hash}:{excel_hash}".encode())
    source_label = f"{xml_path} + {excel_path}"
    
    # Start import run
    supplier_id, source_id = get_supplier_and_source(conn, SUPPLIER_CODE, SOURCE_CODE)
    close_stale_runs(conn, supplier_id)
    run_id = start_import_run(conn, supplier_id, source_id, source_label, source_hash)
    conn.commit()
    
    stats = ImportStats()
    existing_hashes: dict[str, str] = {}
    batch: list[dict[str, Any]] = []
    batch_number = 0
    
    try:
        for index, item in enumerate(merged_products, start=1):
            stats.rows_total += 1
            batch.append(item)
            
            if len(batch) >= BATCH_SIZE:
                batch_number += 1
                flush_product_batch(conn, supplier_id, run_id, batch, existing_hashes, stats)
                _report(
                    progress,
                    (
                        f"  batch {batch_number}: products {stats.rows_total:,}/{len(merged_products):,} | "
                        f"db {count_supplier_products(conn, supplier_id):,} | "
                        f"new {stats.rows_imported:,} | updated {stats.rows_updated:,} | "
                        f"unchanged {stats.rows_skipped:,}"
                    ),
                )
        
        if batch:
            batch_number += 1
            flush_product_batch(conn, supplier_id, run_id, batch, existing_hashes, stats)
            _report(
                progress,
                (
                    f"  batch {batch_number}: products {stats.rows_total:,}/{len(merged_products):,} | "
                    f"db {count_supplier_products(conn, supplier_id):,} | "
                    f"new {stats.rows_imported:,} | updated {stats.rows_updated:,} | "
                    f"unchanged {stats.rows_skipped:,}"
                ),
            )
        
        status = "success" if stats.rows_errors == 0 else "partial"
        finish_import_run(conn, run_id, stats, status, None)
        conn.commit()
        _report(progress, "Import completed.")
        return stats
    except Exception as exc:
        conn.rollback()
        finish_import_run(conn, run_id, stats, "failed", str(exc))
        conn.commit()
        raise
