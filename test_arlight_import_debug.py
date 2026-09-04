#!/usr/bin/env python
"""Debug Arlight import."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[0]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from staging.importers.arlight import parse_xml_products, parse_excel_price, merge_arlight_data

xml_path = r"D:\projects\1C\Арлайт\products.xml"
excel_path = r"D:\projects\1C\Арлайт\Прайс от 18.08.2026.xlsx"

print("Parsing XML...")
xml_products = parse_xml_products(xml_path)
print(f"XML products: {len(xml_products)}")

print("Parsing Excel...")
excel_data = parse_excel_price(excel_path)
print(f"Excel SKUs: {len(excel_data)}")

print("Merging...")
merged = merge_arlight_data(xml_products, excel_data)
print(f"Merged products: {len(merged)}")

print("Sample merged product:")
if merged:
    print(f"  Keys: {list(merged[0].keys())}")
    print(f"  Has description: {'description' in merged[0]}")
