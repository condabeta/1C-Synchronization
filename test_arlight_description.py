#!/usr/bin/env python
"""Test Arlight description extraction."""

import xml.etree.ElementTree as ET

xml_path = r"D:\projects\1C\Арлайт\products.xml"
tree = ET.parse(xml_path)
root = tree.getroot()

products = root.findall(".//product")
print(f"Total products in XML: {len(products)}")

with_desc = 0
without_desc = 0

for i, product_elem in enumerate(products[:100]):  # Check first 100
    article = product_elem.findtext("article")
    
    description = ""
    for text_elem in product_elem.findall("texts/text"):
        text_type = text_elem.get("type")
        if text_type == "descript":
            description = text_elem.text if text_elem.text else ""
    
    if description:
        with_desc += 1
    else:
        without_desc += 1

print(f"\nFirst 100 products:")
print(f"  With description: {with_desc}")
print(f"  Without description: {without_desc}")

# Show a sample with description
for product_elem in products[:5]:
    article = product_elem.findtext("article")
    name = product_elem.findtext("name")
    
    description = ""
    for text_elem in product_elem.findall("texts/text"):
        text_type = text_elem.get("type")
        if text_type == "descript":
            description = text_elem.text if text_elem.text else ""
    
    print(f"\nSKU: {article}")
    print(f"  Name: {name[:60]}...")
    print(f"  Description: {description[:100] if description else 'NULL'}...")
