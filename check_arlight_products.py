#!/usr/bin/env python
"""Check Arlight products state."""

import pymysql
from staging.db import db_session, fetch_all

with db_session() as conn:
    # Check products with internal_sku starting with 'arlight:'
    arlight_products = fetch_all(conn, """
        SELECT id, internal_sku, name 
        FROM products 
        WHERE internal_sku LIKE %s
        LIMIT 10
    """, ('arlight:%',))
    
    print(f"Products with internal_sku starting with 'arlight:': {len(arlight_products)}")
    for p in arlight_products:
        print(f"  ID: {p['id']}, SKU: {p['internal_sku']}, Name: {p['name'][:50]}...")
    
    # Check supplier_products
    supplier_products = fetch_all(conn, """
        SELECT COUNT(*) as cnt
        FROM supplier_products 
        WHERE supplier_id = (SELECT id FROM suppliers WHERE code = 'arlight')
    """)
    
    print(f"\nArlight supplier_products: {supplier_products[0]['cnt']}")
