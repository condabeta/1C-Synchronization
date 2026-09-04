#!/usr/bin/env python
"""Calculate total products that can pass automated inspection across all suppliers."""

import pymysql
from staging.db import db_session, fetch_one, fetch_all

with db_session() as conn:
    # Calculate products that meet all criteria across all suppliers
    result = fetch_one(conn, """
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN 
                p.description IS NOT NULL AND p.description != ''
                AND EXISTS (SELECT 1 FROM product_images WHERE product_id = p.id AND is_active = 1)
                AND p.price IS NOT NULL AND p.price > 0
                AND p.stock_qty IS NOT NULL AND p.stock_qty > 0
                THEN 1 ELSE 0 END) as meets_all_criteria
        FROM products p
    """)
    
    if result:
        total = result['total']
        meets_all = result['meets_all_criteria']
        
        print(f"Total Products in Database: {total}")
        print(f"Products Meeting ALL Criteria (images, description, price > 0, stock > 0): {meets_all}")
        print(f"Pass Rate: {meets_all/total*100:.2f}%")
    
    # Break down by supplier
    suppliers = fetch_all(conn, """
        SELECT 
            s.code,
            COUNT(*) as total,
            SUM(CASE WHEN 
                p.description IS NOT NULL AND p.description != ''
                AND EXISTS (SELECT 1 FROM product_images WHERE product_id = p.id AND is_active = 1)
                AND p.price IS NOT NULL AND p.price > 0
                AND p.stock_qty IS NOT NULL AND p.stock_qty > 0
                THEN 1 ELSE 0 END) as meets_all_criteria
        FROM products p
        LEFT JOIN suppliers s ON s.id = p.primary_supplier_id
        GROUP BY s.code
        ORDER BY meets_all_criteria DESC
    """)
    
    print(f"\nBy Supplier:")
    for s in suppliers:
        code = s['code'] or 'NULL'
        total = s['total']
        meets_all = s['meets_all_criteria']
        rate = meets_all/total*100 if total > 0 else 0
        print(f"  {code}: {meets_all}/{total} ({rate:.2f}%)")
