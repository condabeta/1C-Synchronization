#!/usr/bin/env python
"""Delete archived Arlight products (obsolete = -1)."""

import xml.etree.ElementTree as ET
from staging.db import db_session, fetch_all

# Load XML to find obsolete products
xml_path = r"D:\projects\1C\Арлайт\products.xml"
tree = ET.parse(xml_path)
root = tree.getroot()

# Find SKUs with obsolete = -1
obsolete_skus = set()
for product in root.findall(".//product"):
    article = product.findtext("article")
    obsolete = product.findtext("obsolete")
    if obsolete and obsolete.strip() == "-1":
        obsolete_skus.add(article)

print(f"Found {len(obsolete_skus)} obsolete SKUs in XML")

with db_session() as conn:
    # Delete obsolete products from supplier_products
    print("Deleting obsolete Arlight products from supplier_products...")
    with conn.cursor() as cur:
        if obsolete_skus:
            placeholders = ','.join(['%s'] * len(obsolete_skus))
            cur.execute(f"""
                DELETE FROM supplier_products 
                WHERE supplier_id = (SELECT id FROM suppliers WHERE code = 'arlight')
                AND supplier_sku IN ({placeholders})
            """, tuple(obsolete_skus))
            deleted_sp = cur.rowcount
            print(f"  Deleted from supplier_products: {deleted_sp}")
        else:
            print("  No obsolete SKUs to delete")
    
    conn.commit()
    print("Deletion complete.")
