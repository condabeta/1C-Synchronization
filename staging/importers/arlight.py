from __future__ import annotations

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


def parse_excel_price(excel_path: str) -> dict[str, dict[str, Any]]:
    """Parse Excel price file and return dict by SKU."""
    _report(None, f"Parsing Excel price file: {excel_path}")
    df = pd.read_excel(excel_path, header=None)
    
    # Find header row (contains "Артикул")
    header_row = None
    for i, row in df.iterrows():
        if any("Артикул" in str(cell) for cell in row if pd.notna(cell)):
            header_row = i
            break
    
    if header_row is None:
        raise ValueError("Could not find header row with 'Артикул' in Excel file")
    
    # Read with proper header
    df = pd.read_excel(excel_path, header=header_row)
    
    price_data = {}
    for _, row in df.iterrows():
        sku = clean(row.get("Артикул"))
        if not sku:
            continue
        
        price_data[sku] = {
            "stock_qty": parse_decimal(row.get("Склад")),
            "price": parse_decimal(row.get("Цена (руб.)")),
            "country": clean(row.get("Страна")),
            "excel_description": clean(row.get("Описание")),
        }
    
    _report(None, f"Parsed {len(price_data)} SKUs from Excel")
    return price_data


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
            "brand": clean(product_elem.findtext("brand")),
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
            "supplier_category": "",  # Could be extracted from groups if needed
            "supplier_category_path": "",
            "price": price_info.get("price"),
            "price_retail": price_info.get("price"),
            "price_old": None,
            "stock_qty": price_info.get("stock_qty"),
            "is_available": 1 if price_info.get("stock_qty", 0) > 0 else 0,
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
                "price": str(merged_product["price"]) if merged_product["price"] else None,
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
