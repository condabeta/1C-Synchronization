#!/usr/bin/env python
"""Analyze rejected products by supplier and reason."""

from staging.db import db_session, fetch_all

with db_session() as conn:
    print("=== REJECTED PRODUCTS ANALYSIS ===\n")
    
    # Rejections by supplier
    print("Rejections by supplier:")
    rows = fetch_all(conn, """
        SELECT s.code AS supplier_code, s.name AS supplier_name, COUNT(*) AS cnt
        FROM moderation_queue mq
        JOIN products p ON p.id = mq.product_id
        LEFT JOIN suppliers s ON s.id = p.primary_supplier_id
        WHERE mq.status = 'rejected'
        GROUP BY s.id, s.code, s.name
        ORDER BY cnt DESC
    """)
    for row in rows:
        print(f"  {row['supplier_name']} ({row['supplier_code']}): {row['cnt']}")
    
    print("\nRejection reasons by supplier:")
    rows = fetch_all(conn, """
        SELECT 
            s.code AS supplier_code, 
            s.name AS supplier_name,
            mq.queue_reason,
            COUNT(*) AS cnt
        FROM moderation_queue mq
        JOIN products p ON p.id = mq.product_id
        LEFT JOIN suppliers s ON s.id = p.primary_supplier_id
        WHERE mq.status = 'rejected'
        GROUP BY s.id, s.code, s.name, mq.queue_reason
        ORDER BY s.code, cnt DESC
    """)
    
    current_supplier = None
    for row in rows:
        if row['supplier_code'] != current_supplier:
            current_supplier = row['supplier_code']
            print(f"\n  {row['supplier_name']} ({row['supplier_code']}):")
        print(f"    {row['queue_reason']}: {row['cnt']}")
    
    print("\n=== DATA QUALITY ISSUES BY SUPPLIER ===\n")
    
    # Check for missing images by supplier
    print("Products missing images (pending + rejected):")
    rows = fetch_all(conn, """
        SELECT 
            s.code AS supplier_code, 
            s.name AS supplier_name,
            COUNT(*) AS cnt
        FROM products p
        LEFT JOIN suppliers s ON s.id = p.primary_supplier_id
        WHERE NOT EXISTS (SELECT 1 FROM product_images WHERE product_id = p.id AND is_active = 1)
        AND p.status IN ('pending_moderation', 'rejected')
        GROUP BY s.id, s.code, s.name
        ORDER BY cnt DESC
    """)
    for row in rows:
        print(f"  {row['supplier_name']} ({row['supplier_code']}): {row['cnt']}")
    
    print("\nProducts missing description (pending + rejected):")
    rows = fetch_all(conn, """
        SELECT 
            s.code AS supplier_code, 
            s.name AS supplier_name,
            COUNT(*) AS cnt
        FROM products p
        LEFT JOIN suppliers s ON s.id = p.primary_supplier_id
        WHERE (p.description IS NULL OR p.description = '')
        AND p.status IN ('pending_moderation', 'rejected')
        GROUP BY s.id, s.code, s.name
        ORDER BY cnt DESC
    """)
    for row in rows:
        print(f"  {row['supplier_name']} ({row['supplier_code']}): {row['cnt']}")
    
    print("\nProducts with invalid price (<= 0) (pending + rejected):")
    rows = fetch_all(conn, """
        SELECT 
            s.code AS supplier_code, 
            s.name AS supplier_name,
            COUNT(*) AS cnt
        FROM products p
        LEFT JOIN suppliers s ON s.id = p.primary_supplier_id
        WHERE (p.price IS NULL OR p.price <= 0)
        AND p.status IN ('pending_moderation', 'rejected')
        GROUP BY s.id, s.code, s.name
        ORDER BY cnt DESC
    """)
    for row in rows:
        print(f"  {row['supplier_name']} ({row['supplier_code']}): {row['cnt']}")
