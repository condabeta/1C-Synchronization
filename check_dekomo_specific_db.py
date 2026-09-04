#!/usr/bin/env python
"""Check if specific SKUs with descriptions are in the database."""

from staging.db import db_session, fetch_all

target_skus = ["EG_82844", "EG_86654", "EG_92206", "CL103311", "CL109321"]

with db_session() as conn:
    placeholders = ','.join(['%s'] * len(target_skus))
    results = fetch_all(conn, f"""
        SELECT sp.supplier_sku, sp.name, sp.description, sp.price, sp.stock_qty
        FROM supplier_products sp
        JOIN suppliers s ON s.id = sp.supplier_id
        WHERE s.code = 'dekomo'
        AND sp.supplier_sku IN ({placeholders})
    """, tuple(target_skus))
    
    print(f"Found {len(results)} products in database:")
    for row in results:
        print(f"\nSKU: {row['supplier_sku']}")
        print(f"  Name: {row['name'][:60]}...")
        print(f"  Description: {row['description'][:100] if row['description'] else '(empty)'}")
        print(f"  Price: {row['price']}")
        print(f"  Stock: {row['stock_qty']}")
