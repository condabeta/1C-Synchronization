#!/usr/bin/env python
"""Check Dekomo products for automatic moderation."""

from staging.db import db_session, fetch_all, fetch_one

with db_session() as conn:
    # Check if Dekomo products are in moderation queue
    queue_count = fetch_one(conn, """
        SELECT COUNT(*) as cnt
        FROM moderation_queue mq
        JOIN supplier_products sp ON sp.id = mq.supplier_product_id
        JOIN suppliers s ON s.id = sp.supplier_id
        WHERE s.code = 'dekomo'
    """)
    
    print(f"Dekomo products in moderation queue: {queue_count['cnt'] if queue_count else 0}")
    
    # Check unmatched Dekomo supplier products
    unmatched = fetch_one(conn, """
        SELECT COUNT(*) as cnt
        FROM supplier_products sp
        JOIN suppliers s ON s.id = sp.supplier_id
        WHERE s.code = 'dekomo' AND sp.match_status = 'unmatched'
    """)
    
    print(f"Unmatched Dekomo supplier products: {unmatched['cnt'] if unmatched else 0}")
    
    # Analyze unmatched Dekomo products for automatic approval criteria
    analysis = fetch_one(conn, """
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN sp.images_json IS NOT NULL AND JSON_LENGTH(sp.images_json) > 0 THEN 1 ELSE 0 END) as has_images,
            SUM(CASE WHEN sp.description IS NOT NULL AND sp.description != '' THEN 1 ELSE 0 END) as has_description,
            SUM(CASE WHEN sp.price IS NOT NULL AND sp.price > 0 THEN 1 ELSE 0 END) as has_price,
            SUM(CASE WHEN sp.stock_qty IS NOT NULL AND sp.stock_qty > 0 THEN 1 ELSE 0 END) as has_stock,
            SUM(CASE WHEN 
                sp.images_json IS NOT NULL AND JSON_LENGTH(sp.images_json) > 0
                AND sp.description IS NOT NULL AND sp.description != ''
                AND sp.price IS NOT NULL AND sp.price > 0
                AND sp.stock_qty IS NOT NULL AND sp.stock_qty > 0
                THEN 1 ELSE 0 END) as meets_all_criteria
        FROM supplier_products sp
        JOIN suppliers s ON s.id = sp.supplier_id
        WHERE s.code = 'dekomo' AND sp.match_status = 'unmatched'
    """)
    
    if analysis:
        total = analysis['total']
        print(f"\nUnmatched Dekomo products analysis:")
        print(f"  Total: {total}")
        print(f"  Has images: {analysis['has_images']} ({analysis['has_images']/total*100:.2f}%)")
        print(f"  Has description: {analysis['has_description']} ({analysis['has_description']/total*100:.2f}%)")
        print(f"  Has valid price: {analysis['has_price']} ({analysis['has_price']/total*100:.2f}%)")
        print(f"  Has stock: {analysis['has_stock']} ({analysis['has_stock']/total*100:.2f}%)")
        print(f"  Meets ALL criteria (auto-approve): {analysis['meets_all_criteria']} ({analysis['meets_all_criteria']/total*100:.2f}%)")
        print(f"  Would be queued for manual review: {total - analysis['meets_all_criteria']} ({(total-analysis['meets_all_criteria'])/total*100:.2f}%)")
