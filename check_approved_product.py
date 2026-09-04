#!/usr/bin/env python
"""Check the approved product details."""

import pymysql
from staging.db import db_session, fetch_one

with db_session() as conn:
    # Check the approved product
    approved = fetch_one(conn, '''
        SELECT p.id, p.name, p.status, p.description, p.price, p.stock_qty,
               sp.images_json, s.code as supplier_code
        FROM products p
        JOIN supplier_products sp ON p.id = sp.product_id
        JOIN suppliers s ON s.id = sp.supplier_id
        WHERE p.status = 'approved'
        LIMIT 1
    ''')
    
    if approved:
        print('Approved product found:')
        print(f'  ID: {approved["id"]}')
        print(f'  Name: {approved["name"]}')
        print(f'  Supplier: {approved["supplier_code"]}')
        print(f'  Price: {approved["price"]}')
        print(f'  Stock: {approved["stock_qty"]}')
        print(f'  Has description: {bool(approved["description"])}')
        images = approved.get('images_json')
        has_images = bool(images and (isinstance(images, list) and len(images) > 0))
        print(f'  Has images: {has_images}')
    else:
        print('No approved products found')
