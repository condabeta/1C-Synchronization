#!/usr/bin/env python
"""Check data quality of newly added SWG products."""

import pymysql
from staging.db import db_session, fetch_all

with db_session() as conn:
    # Check data quality for recently added SWG products (from the latest import run)
    new_products = fetch_all(conn, '''
        SELECT 
            sp.supplier_sku,
            sp.name,
            sp.images_json,
            sp.description,
            sp.price,
            sp.stock_qty,
            sp.is_available,
            sp.last_seen_at
        FROM supplier_products sp
        JOIN suppliers s ON s.id = sp.supplier_id
        WHERE s.code = 'swg'
        AND sp.import_run_id = (SELECT id FROM import_runs ORDER BY id DESC LIMIT 1)
        ORDER BY sp.id DESC
        LIMIT 20
    ''')
    
    print(f'Sample of newly added SWG products (latest import run):')
    print(f'Total new products: {len(new_products)}')
    print()
    
    # Count missing data in new products
    missing_images = 0
    missing_desc = 0
    invalid_price = 0
    
    for p in new_products:
        images = p.get('images_json')
        if not images or (isinstance(images, list) and len(images) == 0):
            missing_images += 1
        if not p.get('description') or not p.get('description').strip():
            missing_desc += 1
        if not p.get('price') or p.get('price') <= 0:
            invalid_price += 1
    
    print(f'Data quality in sample of {len(new_products)} new products:')
    print(f'  Missing images: {missing_images}')
    print(f'  Missing description: {missing_desc}')
    print(f'  Invalid price: {invalid_price}')
    print()
    
    # Show a few examples
    print('Sample products:')
    for i, p in enumerate(new_products[:5]):
        images = p.get('images_json')
        has_images = bool(images and (isinstance(images, list) and len(images) > 0))
        has_desc = bool(p.get('description') and p.get('description').strip())
        print(f'  {i+1}. {p["supplier_sku"]}: images={has_images}, desc={has_desc}, price={p["price"]}')
