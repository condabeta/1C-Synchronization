#!/usr/bin/env python
"""Check Arlight source files for problematic SKUs."""

import xml.etree.ElementTree as ET
import pandas as pd

# Load XML
xml_path = r"D:\projects\1C\Арлайт\products.xml"
tree = ET.parse(xml_path)
root = tree.getroot()

# Load Excel
excel_path = r"D:\projects\1C\Арлайт\Прайс от 18.08.2026.xlsx"
df = pd.read_excel(excel_path, header=0)

# Problematic SKUs
test_skus = {
    "000821": "Missing price and stock",
    "000825": "Missing price and stock", 
    "000827": "Missing price and stock",
    "003505": "Missing description",
    "003506": "Missing description",
}

print("=== Checking Arlight Source Files ===\n")

for sku, issue in test_skus.items():
    print(f"SKU: {sku} ({issue})")
    
    # Check XML
    offer = root.find(f".//offer[@article='{sku}']")
    if offer:
        name = offer.findtext("name")
        texts = offer.find("texts")
        desc = None
        if texts:
            for text in texts.findall("text"):
                if text.get("type") == "descript":
                    desc = text.text
                    break
        print(f"  XML: Found | Name: {name[:40] if name else 'NULL'}... | Description: {desc[:40] if desc else 'NULL'}...")
    else:
        print(f"  XML: NOT FOUND")
    
    # Check Excel
    excel_row = df[df.iloc[:, 0].astype(str).str.strip() == sku]
    if not excel_row.empty:
        row = excel_row.iloc[0]
        price = row.iloc[4] if len(row) > 4 else None  # Price column
        stock = row.iloc[3] if len(row) > 3 else None  # Stock column
        print(f"  Excel: Found | Price: {price} | Stock: {stock}")
    else:
        print(f"  Excel: NOT FOUND")
    
    print()
