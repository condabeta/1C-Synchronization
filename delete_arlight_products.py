#!/usr/bin/env python
"""Delete Arlight products from products table."""

import pymysql
from staging.db import db_session

with db_session() as conn:
    # Delete products that were created from Arlight supplier_products
    print("Deleting Arlight products from products table...")
    with conn.cursor() as cur:
        cur.execute("""
            DELETE FROM products 
            WHERE id IN (
                SELECT product_id FROM supplier_products 
                WHERE supplier_id = (SELECT id FROM suppliers WHERE code = 'arlight')
            )
        """)
        deleted = cur.rowcount
        print(f"  Deleted: {deleted} products")
    
    conn.commit()
    print("Deletion complete. Now run build_moderation_queue.py")
