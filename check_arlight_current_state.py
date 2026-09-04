#!/usr/bin/env python
"""Check current Arlight product state after deleting archived products."""

from staging.db import db_session, fetch_one

with db_session() as conn:
    result = fetch_one(conn, """
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN sp.images_json IS NOT NULL AND JSON_LENGTH(sp.images_json) > 0 THEN 1 ELSE 0 END) as has_images,
            SUM(CASE WHEN sp.description IS NOT NULL AND sp.description != '' THEN 1 ELSE 0 END) as has_description,
            SUM(CASE WHEN sp.price IS NOT NULL AND sp.price > 0 THEN 1 ELSE 0 END) as has_valid_price,
            SUM(CASE WHEN sp.stock_qty IS NOT NULL AND sp.stock_qty > 0 THEN 1 ELSE 0 END) as has_stock,
            SUM(CASE WHEN 
                sp.images_json IS NOT NULL AND JSON_LENGTH(sp.images_json) > 0
                AND sp.description IS NOT NULL AND sp.description != ''
                AND sp.price IS NOT NULL AND sp.price > 0
                AND sp.stock_qty IS NOT NULL AND sp.stock_qty > 0
                THEN 1 ELSE 0 END) as meets_all_criteria
        FROM supplier_products sp
        JOIN suppliers s ON s.id = sp.supplier_id
        WHERE s.code = 'arlight'
    """)
    
    if result:
        total = result['total']
        has_images = result['has_images']
        has_description = result['has_description']
        has_valid_price = result['has_valid_price']
        has_stock = result['has_stock']
        meets_all = result['meets_all_criteria']
        
        print(f"Arlight Products After Deleting Archived:")
        print(f"  Total products: {total}")
        print(f"  Has images: {has_images} ({has_images/total*100:.2f}%)")
        print(f"  Has description: {has_description} ({has_description/total*100:.2f}%)")
        print(f"  Has valid price: {has_valid_price} ({has_valid_price/total*100:.2f}%)")
        print(f"  Has stock: {has_stock} ({has_stock/total*100:.2f}%)")
        print(f"  Meets ALL criteria: {meets_all} ({meets_all/total*100:.2f}%)")
