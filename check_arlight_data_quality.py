#!/usr/bin/env python
"""Check data quality of Arlight products."""

import pymysql
from staging.db import db_session, fetch_all

with db_session() as conn:
    # Check data quality for Arlight products
    arlight_products = fetch_all(conn, '''
        SELECT 
            sp.supplier_sku,
            sp.name,
            sp.images_json,
            sp.description,
            sp.price,
            sp.stock_qty,
            sp.is_available
        FROM supplier_products sp
        JOIN suppliers s ON s.id = sp.supplier_id
        WHERE s.code = 'arlight'
        ORDER BY sp.id DESC
        LIMIT 100
    ''')
    
    print(f'Sample of Arlight products (first 100):')
    print(f'Total in sample: {len(arlight_products)}')
    print()
    
    # Count missing data in sample
    missing_images = 0
    missing_desc = 0
    invalid_price = 0
    zero_stock = 0
    
    for p in arlight_products:
        images = p.get('images_json')
        if not images or (isinstance(images, list) and len(images) == 0):
            missing_images += 1
        if not p.get('description') or not p.get('description').strip():
            missing_desc += 1
        if not p.get('price') or p.get('price') <= 0:
            invalid_price += 1
        if not p.get('stock_qty') or p.get('stock_qty') <= 0:
            zero_stock += 1
    
    print(f'Data quality in sample of {len(arlight_products)} products:')
    print(f'  Missing images: {missing_images}')
    print(f'  Missing description: {missing_desc}')
    print(f'  Invalid price: {invalid_price}')
    print(f'  Zero stock: {zero_stock}')
    print()
    
    # Check overall Arlight data quality
    print('Overall Arlight data quality:')
    overall = fetch_all(conn, '''
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN sp.images_json IS NULL OR JSON_LENGTH(sp.images_json) = 0 THEN 1 ELSE 0 END) as missing_images,
            SUM(CASE WHEN sp.description IS NULL OR sp.description = '' THEN 1 ELSE 0 END) as missing_desc,
            SUM(CASE WHEN sp.price IS NULL OR sp.price <= 0 THEN 1 ELSE 0 END) as invalid_price,
            SUM(CASE WHEN sp.stock_qty IS NULL OR sp.stock_qty <= 0 THEN 1 ELSE 0 END) as zero_stock
        FROM supplier_products sp
        JOIN suppliers s ON s.id = sp.supplier_id
        WHERE s.code = 'arlight'
    ''')
    
    if overall:
        stats = overall[0]
        print(f'  Total products: {stats["total"]}')
        print(f'  Missing images: {stats["missing_images"]}')
        print(f'  Missing description: {stats["missing_desc"]}')
        print(f'  Invalid price: {stats["invalid_price"]}')
        print(f'  Zero stock: {stats["zero_stock"]}')
