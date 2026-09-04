#!/usr/bin/env python
"""Check unmatched Arlight products."""

from staging.db import db_session, fetch_one

with db_session() as conn:
    result = fetch_one(conn, """
        SELECT COUNT(*) as count
        FROM supplier_products sp
        JOIN suppliers s ON s.id = sp.supplier_id
        WHERE s.code = 'arlight'
        AND sp.match_status = 'unmatched'
    """)
    
    print(f"Unmatched Arlight products: {result['count']}")
