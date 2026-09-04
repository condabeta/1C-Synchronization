#!/usr/bin/env python
"""Check Arlight XML for obsolete tags."""

import xml.etree.ElementTree as ET

xml_path = r"D:\projects\1C\Арлайт\products.xml"
tree = ET.parse(xml_path)
root = tree.getroot()

# Check if obsolete tag exists
products = root.findall(".//product[@article]")
print(f"Total products in XML: {len(products)}")

# Check first few products for obsolete tag
for i, product in enumerate(products[:10]):
    article = product.get("article")
    obsolete = product.findtext("obsolete")
    print(f"  {article}: obsolete = {obsolete}")
