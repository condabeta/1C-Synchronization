#!/usr/bin/env python
"""Delete Crystal products from products table."""

import pymysql
from staging.db import db_session

with db_session() as conn:
    # Delete products with internal_sku starting with 'crystal:'
    print("Deleting Crystal products from products table...")
    with conn.cursor() as cur:
        cur.execute("""
            DELETE FROM products 
            WHERE internal_sku LIKE %s
        """, ('crystal:%',))
        deleted = cur.rowcount
        print(f"  Deleted: {deleted} products")
    
    conn.commit()
    print("Deletion complete. Now run build_moderation_queue.py")
