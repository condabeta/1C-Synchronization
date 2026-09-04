#!/usr/bin/env python
"""Get examples of problematic Arlight SKUs that exist in source files."""

import xml.etree.ElementTree as ET
import pandas as pd
from staging.db import db_session, fetch_all

# Load XML
xml_path = r"D:\projects\1C\Арлайт\products.xml"
tree = ET.parse(xml_path)
root = tree.getroot()

# Load Excel
excel_path = r"D:\projects\1C\Арлайт\Прайс от 18.08.2026.xlsx"
df = pd.read_excel(excel_path, header=0)

# Build SKU sets from source files
xml_skus = {offer.get("article") for offer in root.findall(".//offer[@article]")}
excel_skus = set(df.iloc[:, 0].astype(str).str.strip().dropna())

with db_session() as conn:
    print("=== Arlight Problematic SKUs (in source files) ===\n")
    
    # Missing price (in Excel)
    print("SKUs Missing Price (in Excel):")
    if excel_skus:
        missing_price = fetch_all(conn, """
            SELECT sp.supplier_sku, sp.name, sp.price
            FROM supplier_products sp
            JOIN suppliers s ON s.id = sp.supplier_id
            WHERE s.code = 'arlight'
            AND (sp.price IS NULL OR sp.price <= 0)
            AND sp.supplier_sku IN %s
            LIMIT 5
        """, (tuple(excel_skus),))
        for row in missing_price:
            print(f"  SKU: {row['supplier_sku']} | Name: {row['name'][:50]}... | Price: {row['price']} | Source: Excel")
    else:
        print("  No SKUs found in Excel")
    
    # Missing stock (in Excel)
    print("\nSKUs Missing Stock (in Excel):")
    if excel_skus:
        missing_stock = fetch_all(conn, """
            SELECT sp.supplier_sku, sp.name, sp.stock_qty
            FROM supplier_products sp
            JOIN suppliers s ON s.id = sp.supplier_id
            WHERE s.code = 'arlight'
            AND (sp.stock_qty IS NULL OR sp.stock_qty <= 0)
            AND sp.supplier_sku IN %s
            LIMIT 5
        """, (tuple(excel_skus),))
        for row in missing_stock:
            print(f"  SKU: {row['supplier_sku']} | Name: {row['name'][:50]}... | Stock: {row['stock_qty']} | Source: Excel")
    else:
        print("  No SKUs found in Excel")
    
    # Missing description (in XML)
    print("\nSKUs Missing Description (in XML):")
    if xml_skus:
        missing_desc = fetch_all(conn, """
            SELECT sp.supplier_sku, sp.name, sp.description
            FROM supplier_products sp
            JOIN suppliers s ON s.id = sp.supplier_id
            WHERE s.code = 'arlight'
            AND (sp.description IS NULL OR sp.description = '')
            AND sp.supplier_sku IN %s
            LIMIT 5
        """, (tuple(xml_skus),))
        for row in missing_desc:
            print(f"  SKU: {row['supplier_sku']} | Name: {row['name'][:50]}... | Description: NULL | Source: XML")
    else:
        print("  No SKUs found in XML")
