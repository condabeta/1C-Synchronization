#!/usr/bin/env python
"""Check if SWG YML has descriptions."""

import xml.etree.ElementTree as ET

yml_path = r"D:\projects\1C\SWG\yml_updated.xml"

tree = ET.parse(yml_path)
root = tree.getroot()

offers = root.findall(".//offer")
print(f"Total offers: {len(offers)}")

# Check first 5 offers for description
for i, offer in enumerate(offers[:5]):
    sku = offer.get("id")
    name = offer.findtext("model")
    description = offer.findtext("description")
    
    print(f"\nOffer {i+1}:")
    print(f"  SKU: {sku}")
    print(f"  Name: {name[:60] if name else 'NULL'}...")
    print(f"  Description: {description[:100] if description else 'NULL'}...")
