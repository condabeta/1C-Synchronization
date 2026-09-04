#!/usr/bin/env python
"""Delete all Dekomo products to force re-import with descriptions."""

from staging.db import db_session

with db_session() as conn:
    # Delete Dekomo products from supplier_products
    print("Deleting all Dekomo products from supplier_products...")
    with conn.cursor() as cur:
        cur.execute("""
            DELETE FROM supplier_products 
            WHERE supplier_id = (SELECT id FROM suppliers WHERE code = 'dekomo')
        """)
        deleted = cur.rowcount
        print(f"  Deleted: {deleted} products")
    
    conn.commit()
    print("Deletion complete.")
