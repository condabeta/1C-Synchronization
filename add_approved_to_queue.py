#!/usr/bin/env python
"""Add approved products to moderation queue."""

import pymysql
from staging.db import db_session, fetch_one

with db_session() as conn:
    # Add approved products to moderation queue
    print("Adding approved products to moderation queue...")
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO moderation_queue (product_id, status, queue_reason, priority, created_at)
            SELECT p.id, 'pending', 'manual', 5, NOW()
            FROM products p
            WHERE p.status = 'approved'
            AND NOT EXISTS (
                SELECT 1 FROM moderation_queue mq 
                WHERE mq.product_id = p.id
            )
        """)
        added = cur.rowcount
        print(f"  Approved products added to queue: {added}")
    
    # Check current status
    print("\nCurrent moderation queue status:")
    queue_stats = fetch_one(conn, """
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending,
            SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) as approved,
            SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) as rejected
        FROM moderation_queue
    """)
    print(f"  Queue: total={queue_stats['total']}, pending={queue_stats['pending']}, approved={queue_stats['approved']}, rejected={queue_stats['rejected']}")
    
    print("\nCompleted!")
