#!/usr/bin/env python
"""Debug upsert with description."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[0]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from staging.db import db_session
from staging.importers.arlight import parse_xml_products, parse_excel_price, merge_arlight_data
from staging.importers.common import get_supplier_and_source, upsert_product_batch, ImportStats

xml_path = r"D:\projects\1C\Арлайт\products.xml"
excel_path = r"D:\projects\1C\Арлайт\Прайс от 18.08.2026.xlsx"

print("Parsing data...")
xml_products = parse_xml_products(xml_path)
excel_data = parse_excel_price(excel_path)
merged = merge_arlight_data(xml_products, excel_data)

print(f"Merged {len(merged)} products")
print("Testing upsert with first product...")

with db_session() as conn:
    supplier_id, source_id = get_supplier_and_source(conn, "arlight", "xml_excel")
    
    # Try to upsert just one product
    batch = [merged[0]]
    stats = ImportStats()
    
    try:
        upsert_product_batch(conn, supplier_id, 1, batch, {}, stats)
        print("Upsert successful!")
        conn.rollback()  # Don't commit
    except Exception as e:
        print(f"Upsert failed: {e}")
        print(f"Batch keys: {list(batch[0].keys())}")
        print(f"Batch sample: {batch[0]}")
