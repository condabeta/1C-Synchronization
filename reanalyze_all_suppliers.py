#!/usr/bin/env python
"""Re-analyze data quality for all suppliers directly from database."""

import pymysql
from staging.db import db_session, fetch_all

with db_session() as conn:
    suppliers = ['viasvet', 'jazzway', 'crystal', 'swg', 'dekomo', 'arlight']
    
    print("=== DATA QUALITY ANALYSIS FROM DATABASE ===\n")
    
    for supplier_code in suppliers:
        print(f"--- {supplier_code.upper()} ---")
        
        result = fetch_all(conn, """
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN sp.images_json IS NOT NULL AND JSON_LENGTH(sp.images_json) > 0 THEN 1 ELSE 0 END) as has_images,
                SUM(CASE WHEN sp.description IS NOT NULL AND sp.description != '' THEN 1 ELSE 0 END) as has_description,
                SUM(CASE WHEN sp.price IS NOT NULL AND sp.price > 0 THEN 1 ELSE 0 END) as has_valid_price,
                SUM(CASE WHEN sp.stock_qty IS NOT NULL AND sp.stock_qty > 0 THEN 1 ELSE 0 END) as has_stock
            FROM supplier_products sp
            JOIN suppliers s ON s.id = sp.supplier_id
            WHERE s.code = %s
        """, (supplier_code,))
        
        if result:
            r = result[0]
            total = r['total']
            has_images = r['has_images']
            has_description = r['has_description']
            has_valid_price = r['has_valid_price']
            has_stock = r['has_stock']
            
            print(f"  Total products: {total}")
            print(f"  Has images: {has_images} ({has_images/total*100:.2f}%)")
            print(f"  Has description: {has_description} ({has_description/total*100:.2f}%)")
            print(f"  Has valid price: {has_valid_price} ({has_valid_price/total*100:.2f}%)")
            print(f"  Has stock: {has_stock} ({has_stock/total*100:.2f}%)")
            
            # Show sample product
            sample = fetch_all(conn, """
                SELECT sp.supplier_sku, sp.name, sp.description, sp.images_json, sp.price, sp.stock_qty
                FROM supplier_products sp
                JOIN suppliers s ON s.id = sp.supplier_id
                WHERE s.code = %s
                LIMIT 1
            """, (supplier_code,))
            
            if sample:
                s = sample[0]
                print(f"\n  Sample product:")
                print(f"    SKU: {s['supplier_sku']}")
                print(f"    Name: {s['name'][:60]}...")
                print(f"    Description: {s['description'][:80] if s['description'] else 'NULL'}...")
                print(f"    Images: {len(s['images_json']) if s['images_json'] else 0}")
                print(f"    Price: {s['price']}")
                print(f"    Stock: {s['stock_qty']}")
        
        print()
