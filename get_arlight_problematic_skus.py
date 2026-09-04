#!/usr/bin/env python
"""Get examples of problematic Arlight SKUs for diagnosis."""

from staging.db import db_session, fetch_all

with db_session() as conn:
    print("=== Arlight Problematic SKUs ===\n")
    
    # Missing price
    print("SKUs Missing Price:")
    missing_price = fetch_all(conn, """
        SELECT sp.supplier_sku, sp.name, sp.price
        FROM supplier_products sp
        JOIN suppliers s ON s.id = sp.supplier_id
        WHERE s.code = 'arlight'
        AND (sp.price IS NULL OR sp.price <= 0)
        LIMIT 5
    """)
    for row in missing_price:
        print(f"  SKU: {row['supplier_sku']} | Name: {row['name'][:50]}... | Price: {row['price']}")
    
    # Missing stock
    print("\nSKUs Missing Stock:")
    missing_stock = fetch_all(conn, """
        SELECT sp.supplier_sku, sp.name, sp.stock_qty
        FROM supplier_products sp
        JOIN suppliers s ON s.id = sp.supplier_id
        WHERE s.code = 'arlight'
        AND (sp.stock_qty IS NULL OR sp.stock_qty <= 0)
        LIMIT 5
    """)
    for row in missing_stock:
        print(f"  SKU: {row['supplier_sku']} | Name: {row['name'][:50]}... | Stock: {row['stock_qty']}")
    
    # Missing images
    print("\nSKUs Missing Images:")
    missing_images = fetch_all(conn, """
        SELECT sp.supplier_sku, sp.name, sp.images_json
        FROM supplier_products sp
        JOIN suppliers s ON s.id = sp.supplier_id
        WHERE s.code = 'arlight'
        AND (sp.images_json IS NULL OR JSON_LENGTH(sp.images_json) = 0)
        LIMIT 5
    """)
    for row in missing_images:
        print(f"  SKU: {row['supplier_sku']} | Name: {row['name'][:50]}... | Images: {len(row['images_json']) if row['images_json'] else 0}")
    
    # Missing description
    print("\nSKUs Missing Description:")
    missing_desc = fetch_all(conn, """
        SELECT sp.supplier_sku, sp.name, sp.description
        FROM supplier_products sp
        JOIN suppliers s ON s.id = sp.supplier_id
        WHERE s.code = 'arlight'
        AND (sp.description IS NULL OR sp.description = '')
        LIMIT 5
    """)
    for row in missing_desc:
        print(f"  SKU: {row['supplier_sku']} | Name: {row['name'][:50]}... | Description: {'NULL' if not row['description'] else row['description'][:30]}...")
