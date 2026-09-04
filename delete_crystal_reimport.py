#!/usr/bin/env python
"""Delete Crystal products and re-import to populate descriptions."""

import pymysql
from staging.db import db_session

with db_session() as conn:
    # Delete Crystal products
    print("Deleting Crystal products...")
    with conn.cursor() as cur:
        cur.execute("DELETE FROM supplier_products WHERE supplier_id = (SELECT id FROM suppliers WHERE code = 'crystal')")
        deleted = cur.rowcount
        print(f"  Deleted: {deleted} products")
    
    conn.commit()
    print("Deletion complete. Now run import_crystal.py")
