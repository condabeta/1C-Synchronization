#!/usr/bin/env python
"""Delete archived Arlight products from products table."""

from staging.db import db_session

with db_session() as conn:
    # Delete Arlight products that no longer exist in supplier_products
    print("Deleting Arlight products from products table (archived)...")
    with conn.cursor() as cur:
        cur.execute("""
            DELETE p FROM products p
            LEFT JOIN supplier_products sp ON p.id = sp.product_id
            WHERE p.internal_sku LIKE 'arlight:%'
            AND sp.id IS NULL
        """)
        deleted = cur.rowcount
        print(f"  Deleted: {deleted} products")
    
    conn.commit()
    print("Deletion complete.")
