#!/usr/bin/env python
"""Analyze Arlight data files."""

import pandas as pd
import xml.etree.ElementTree as ET

# Analyze Excel file
print("=== Analyzing Excel Price File ===")
df = pd.read_excel(r'D:\projects\1C\Арлайт\Прайс от 18.08.2026.xlsx', header=None)
print(f"Shape: {df.shape}")
print(f"First 20 rows:")
for i in range(min(20, len(df))):
    row_data = [str(x) if pd.notna(x) else '' for x in df.iloc[i]]
    print(f"  {i}: {row_data}")

# Analyze XML file
print("\n=== Analyzing XML Products File ===")
tree = ET.parse(r'D:\projects\1C\Арлайт\products.xml')
root = tree.getroot()

print(f"Root tag: {root.tag}")
print(f"Attributes: {root.attrib}")

# Count products
products = root.findall('.//product')
print(f"Total products: {len(products)}")

# Sample product structure
if products:
    sample = products[0]
    print(f"\nSample product structure:")
    for child in sample:
        print(f"  {child.tag}: {child.text if len(child) == 0 else f'[{len(list(child))} children]'}")
