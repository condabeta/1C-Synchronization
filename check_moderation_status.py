#!/usr/bin/env python
"""Check moderation status."""

from staging.db import db_session, fetch_all

with db_session() as conn:
    print("Moderation queue status:")
    rows = fetch_all(conn, """
        SELECT status, COUNT(*) as cnt 
        FROM moderation_queue 
        GROUP BY status
    """)
    for row in rows:
        print(f"  {row['status']}: {row['cnt']}")
    
    print("\nProducts status:")
    rows = fetch_all(conn, """
        SELECT status, COUNT(*) as cnt 
        FROM products 
        GROUP BY status
    """)
    for row in rows:
        print(f"  {row['status']}: {row['cnt']}")
