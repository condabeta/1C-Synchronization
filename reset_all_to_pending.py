#!/usr/bin/env python
"""Reset all rejected products to pending status and ensure all products are in moderation queue."""

import pymysql
from staging.db import db_session, fetch_one

with db_session() as conn:
    # Reset all rejected products to pending_moderation
    print("Resetting rejected products to pending_moderation...")
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE products 
            SET status = 'pending_moderation' 
            WHERE status = 'rejected'
        """)
        products_reset = cur.rowcount
        print(f"  Products reset: {products_reset}")
    
    # Reset all rejected moderation queue items to pending
    print("Resetting rejected moderation queue items to pending...")
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE moderation_queue 
            SET status = 'pending' 
            WHERE status = 'rejected'
        """)
        queue_reset = cur.rowcount
        print(f"  Queue items reset: {queue_reset}")
    
    # Check current status
    print("\nCurrent status:")
    
    # Product status counts
    product_stats = fetch_one(conn, """
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN status = 'pending_moderation' THEN 1 ELSE 0 END) as pending,
            SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) as approved,
            SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) as rejected
        FROM products
    """)
    print(f"  Products: total={product_stats['total']}, pending={product_stats['pending']}, approved={product_stats['approved']}, rejected={product_stats['rejected']}")
    
    # Queue status counts
    queue_stats = fetch_one(conn, """
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending,
            SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) as approved,
            SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) as rejected
        FROM moderation_queue
    """)
    print(f"  Queue: total={queue_stats['total']}, pending={queue_stats['pending']}, approved={queue_stats['approved']}, rejected={queue_stats['rejected']}")
    
    # Check if all products are in queue
    not_in_queue = fetch_one(conn, """
        SELECT COUNT(*) as cnt
        FROM products p
        WHERE NOT EXISTS (
            SELECT 1 FROM moderation_queue mq 
            WHERE mq.product_id = p.id
        )
    """)
    print(f"  Products not in queue: {not_in_queue['cnt']}")
    
    print("\nReset completed successfully!")
