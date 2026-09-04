#!/usr/bin/env python
"""Bulk moderation based on automated rules."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from staging.db import db_session, fetch_all
from staging.moderation.service import approve_queue_item, reject_queue_item


def auto_approve_complete_products(conn, supplier_code: str | None = None, limit: int | None = None) -> int:
    """Auto-approve products that meet all quality criteria."""
    where = ["mq.status = 'pending'"]
    params = []
    
    if supplier_code:
        where.append("s.code = %s")
        params.append(supplier_code)
    
    limit_sql = f"LIMIT {int(limit)}" if limit else ""
    
    # Auto-approve criteria: images + description + price > 0 + stock > 0
    rows = fetch_all(conn, f"""
        SELECT mq.id, p.id as product_id
        FROM moderation_queue mq
        JOIN products p ON p.id = mq.product_id
        LEFT JOIN suppliers s ON s.id = p.primary_supplier_id
        WHERE {' AND '.join(where)}
        AND (p.description IS NOT NULL AND p.description != '')
        AND EXISTS (SELECT 1 FROM product_images WHERE product_id = p.id AND is_active = 1)
        AND (p.price IS NOT NULL AND p.price > 0)
        AND (p.stock_qty IS NOT NULL AND p.stock_qty > 0)
        {limit_sql}
    """, params)
    
    approved = 0
    for row in rows:
        if approve_queue_item(conn, row['id'], reviewer='auto_approve', notes='Auto-approved: complete data (images, description, price, stock)'):
            approved += 1
            if approved % 100 == 0:
                conn.commit()
                print(f"  Approved: {approved}")
    
    conn.commit()
    return approved


def auto_reject_missing_critical(conn, supplier_code: str | None = None, limit: int | None = None) -> int:
    """Auto-reject products with critical data issues."""
    where = ["mq.status = 'pending'"]
    params = []
    
    if supplier_code:
        where.append("s.code = %s")
        params.append(supplier_code)
    
    limit_sql = f"LIMIT {int(limit)}" if limit else ""
    
    # Auto-reject criteria: missing images AND description, OR price <= 0, OR stock = 0 and not available on order
    rows = fetch_all(conn, f"""
        SELECT mq.id, p.id as product_id
        FROM moderation_queue mq
        JOIN products p ON p.id = mq.product_id
        LEFT JOIN suppliers s ON s.id = p.primary_supplier_id
        WHERE {' AND '.join(where)}
        AND (
            (p.description IS NULL OR p.description = '')
            AND NOT EXISTS (SELECT 1 FROM product_images WHERE product_id = p.id AND is_active = 1)
            OR (p.price IS NULL OR p.price <= 0)
            OR (p.stock_qty = 0 AND p.is_available = 0)
        )
        {limit_sql}
    """, params)
    
    rejected = 0
    for row in rows:
        reason = []
        if not row.get('description') or not row.get('description').strip():
            reason.append('missing description')
        if not fetch_all(conn, "SELECT 1 FROM product_images WHERE product_id = %s AND is_active = 1 LIMIT 1", (row['product_id'],)):
            reason.append('missing images')
        if not row.get('price') or row.get('price') <= 0:
            reason.append('invalid price')
        if row.get('stock_qty') == 0 and not row.get('is_available'):
            reason.append('no stock and not available')
        
        notes = f"Auto-rejected: {', '.join(reason)}"
        if reject_queue_item(conn, row['id'], reviewer='auto_reject', notes=notes):
            rejected += 1
            if rejected % 100 == 0:
                conn.commit()
                print(f"  Rejected: {rejected}")
    
    conn.commit()
    return rejected


def main() -> int:
    parser = argparse.ArgumentParser(description="Bulk moderation with automated rules")
    parser.add_argument("--supplier", help="Supplier code (dekomo, jazzway, etc.)")
    parser.add_argument("--limit", type=int, help="Max items to process (for testing)")
    parser.add_argument("--approve-complete", action="store_true", help="Auto-approve products with images + description")
    parser.add_argument("--reject-missing", action="store_true", help="Auto-reject products missing images + description")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without executing")
    
    args = parser.parse_args()
    
    if not (args.approve_complete or args.reject_missing):
        print("Error: Specify --approve-complete and/or --reject-missing", file=sys.stderr)
        return 1
    
    with db_session() as conn:
        if args.approve_complete:
            print(f"Auto-approving complete products...")
            if args.dry_run:
                print("  (Dry run - no changes will be made)")
                count = auto_approve_complete_products(conn, args.supplier, args.limit)
                conn.rollback()  # Rollback changes in dry-run mode
            else:
                count = auto_approve_complete_products(conn, args.supplier, args.limit)
            print(f"  Approved: {count}")
        
        if args.reject_missing:
            print(f"Auto-rejecting products missing critical data...")
            if args.dry_run:
                print("  (Dry run - no changes will be made)")
                count = auto_reject_missing_critical(conn, args.supplier, args.limit)
                conn.rollback()  # Rollback changes in dry-run mode
            else:
                count = auto_reject_missing_critical(conn, args.supplier, args.limit)
            print(f"  Rejected: {count}")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
