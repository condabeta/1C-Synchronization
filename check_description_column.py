#!/usr/bin/env python
"""Check if description column exists in supplier_products table."""

import pymysql
from staging.db import db_session, fetch_all

with db_session() as conn:
    # Check table structure
    columns = fetch_all(conn, """
        SHOW COLUMNS FROM supplier_products
    """)
    
    print("Columns in supplier_products table:")
    for col in columns:
        print(f"  {col['Field']}: {col['Type']}")
