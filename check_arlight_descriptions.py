#!/usr/bin/env python
"""Check Arlight descriptions in database."""

import pymysql
from staging.db import db_session, fetch_all

with db_session() as conn:
    # Check if descriptions exist in Arlight products
    arlight = fetch_all(conn, '''
        SELECT sp.supplier_sku, sp.name, sp.description, sp.attributes_json
        FROM supplier_products sp
        JOIN suppliers s ON s.id = sp.supplier_id
        WHERE s.code = 'arlight'
        LIMIT 5
    ''')
    
    print('Sample Arlight products with description:')
    for p in arlight:
        sku = p['supplier_sku']
        name = p['name'][:60] if p['name'] else 'NULL'
        desc = p['description'][:80] if p['description'] else 'NULL'
        attrs = p['attributes_json']
        print(f'SKU: {sku}')
        print(f'  Name: {name}...')
        print(f'  Description: {desc}...')
        print(f'  Attributes: {str(attrs)[:100] if attrs else "NULL"}...')
        print()
