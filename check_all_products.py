#!/usr/bin/env python
"""Check all products and their status."""

from staging.db import db_session, fetch_all

with db_session() as conn:
    print("Supplier products by match_status:")
    rows = fetch_all(conn, """
        SELECT match_status, COUNT(*) as cnt 
        FROM supplier_products 
        GROUP BY match_status
    """)
    for row in rows:
        print(f"  {row['match_status']}: {row['cnt']}")
    
    print("\nProducts by status:")
    rows = fetch_all(conn, """
        SELECT status, COUNT(*) as cnt 
        FROM products 
        GROUP BY status
    """)
    for row in rows:
        print(f"  {row['status']}: {row['cnt']}")
    
    print("\nModeration queue by status:")
    rows = fetch_all(conn, """
        SELECT status, COUNT(*) as cnt 
        FROM moderation_queue 
        GROUP BY status
    """)
    for row in rows:
        print(f"  {row['status']}: {row['cnt']}")
