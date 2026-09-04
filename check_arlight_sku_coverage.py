#!/usr/bin/env python
"""Check which Arlight SKUs in DB exist in source files."""

import xml.etree.ElementTree as ET
import pandas as pd
from staging.db import db_session, fetch_all

# Load XML
xml_path = r"D:\projects\1C\Арлайт\products.xml"
tree = ET.parse(xml_path)
root = tree.getroot()
xml_skus = {product.get("article") for product in root.findall(".//product[@article]")}

# Load Excel with proper header
excel_path = r"D:\projects\1C\Арлайт\Прайс от 18.08.2026.xlsx"
df = pd.read_excel(excel_path, header=9)
excel_skus = set(df["Артикул"].astype(str).str.strip().dropna())

with db_session() as conn:
    # Get all Arlight SKUs in DB
    db_skus = fetch_all(conn, """
        SELECT sp.supplier_sku, sp.name, sp.price, sp.stock_qty
        FROM supplier_products sp
        JOIN suppliers s ON s.id = sp.supplier_id
        WHERE s.code = 'arlight'
    """)
    
    db_sku_set = {row['supplier_sku'] for row in db_skus}
    
    print(f"=== Arlight SKU Coverage ===")
    print(f"  XML SKUs: {len(xml_skus)}")
    print(f"  Excel SKUs: {len(excel_skus)}")
    print(f"  DB SKUs: {len(db_sku_set)}")
    print(f"  DB SKUs in XML: {len(db_sku_set & xml_skus)}")
    print(f"  DB SKUs in Excel: {len(db_sku_set & excel_skus)}")
    print(f"  DB SKUs in NEITHER: {len(db_sku_set - xml_skus - excel_skus)}")
    
    # Show examples of SKUs missing from source files
    missing_from_source = db_sku_set - xml_skus - excel_skus
    if missing_from_source:
        print(f"\nExamples of DB SKUs not in source files (first 10):")
        for sku in list(missing_from_source)[:10]:
            row = next(r for r in db_skus if r['supplier_sku'] == sku)
            print(f"  SKU: {sku} | Name: {row['name'][:50]}... | Price: {row['price']} | Stock: {row['stock_qty']}")
