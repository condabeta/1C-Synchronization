#!/usr/bin/env python
"""Delete Arlight products and re-import to populate descriptions."""

import pymysql
from staging.db import db_session

with db_session() as conn:
    # Delete Arlight products
    print("Deleting Arlight products...")
    with conn.cursor() as cur:
        cur.execute("DELETE FROM supplier_products WHERE supplier_id = (SELECT id FROM suppliers WHERE code = 'arlight')")
        deleted = cur.rowcount
        print(f"  Deleted: {deleted} products")
    
    conn.commit()
    print("Deletion complete. Now run import_arlight.py")
