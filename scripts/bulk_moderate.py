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

COMMIT_EVERY = 100

HAS_IMAGES = "EXISTS (SELECT 1 FROM product_images WHERE product_id = p.id AND is_active = 1)"
HAS_DESCRIPTION = "(p.description IS NOT NULL AND p.description <> '')"
HAS_PRICE = "(p.price IS NOT NULL AND p.price > 0)"
HAS_STOCK = "(p.stock_qty IS NOT NULL AND p.stock_qty > 0)"


def _pending_query(extra_where: str, supplier_code: str | None, limit: int | None) -> tuple[str, list]:
    where = ["mq.status = 'pending'", extra_where]
    params: list = []
    if supplier_code:
        where.append("s.code = %s")
        params.append(supplier_code)
    limit_sql = f"LIMIT {int(limit)}" if limit else ""
    sql = f"""
        SELECT mq.id, p.id AS product_id, p.description, p.price, p.stock_qty, p.is_available,
               {HAS_IMAGES} AS has_images
        FROM moderation_queue mq
        JOIN products p ON p.id = mq.product_id
        LEFT JOIN suppliers s ON s.id = p.primary_supplier_id
        WHERE {' AND '.join(where)}
        {limit_sql}
    """
    return sql, params


def _apply(conn, rows, action, label: str, note_for, dry_run: bool) -> int:
    """Run approve/reject over rows, committing periodically unless dry_run."""
    done = 0
    for row in rows:
        if action(conn, row["id"], reviewer=label, notes=note_for(row)):
            done += 1
            if not dry_run and done % COMMIT_EVERY == 0:
                conn.commit()
                print(f"  {label}: {done}")
    if dry_run:
        # Nothing was committed along the way, so this discards the whole run.
        conn.rollback()
    else:
        conn.commit()
    return done


def auto_approve_complete_products(conn, supplier_code=None, limit=None, dry_run=False) -> int:
    """Auto-approve products that meet all quality criteria."""
    criteria = f"{HAS_DESCRIPTION} AND {HAS_IMAGES} AND {HAS_PRICE} AND {HAS_STOCK}"
    sql, params = _pending_query(criteria, supplier_code, limit)
    rows = fetch_all(conn, sql, params)
    return _apply(
        conn, rows, approve_queue_item, "auto_approve",
        lambda row: "Auto-approved: complete data (images, description, price, stock)",
        dry_run,
    )


def auto_reject_missing_critical(conn, supplier_code=None, limit=None, dry_run=False) -> int:
    """Auto-reject products whose content cannot be published.

    Only content defects reject a product: no description *and* no images, or no
    usable price. Zero stock is deliberately not a rejection reason - an
    out-of-stock product is still a valid catalogue entry.
    """
    criteria = f"((NOT {HAS_DESCRIPTION} AND NOT {HAS_IMAGES}) OR NOT {HAS_PRICE})"
    sql, params = _pending_query(criteria, supplier_code, limit)
    rows = fetch_all(conn, sql, params)

    def note_for(row) -> str:
        reasons = []
        if not (row.get("description") or "").strip():
            reasons.append("missing description")
        if not row.get("has_images"):
            reasons.append("missing images")
        if not row.get("price") or row["price"] <= 0:
            reasons.append("invalid price")
        return f"Auto-rejected: {', '.join(reasons)}"

    return _apply(conn, rows, reject_queue_item, "auto_reject", note_for, dry_run)


def main() -> int:
    parser = argparse.ArgumentParser(description="Bulk moderation with automated rules")
    parser.add_argument("--supplier", help="Supplier code (dekomo, jazzway, etc.)")
    parser.add_argument("--limit", type=int, help="Max items to process (for testing)")
    parser.add_argument("--approve-complete", action="store_true",
                        help="Auto-approve products with images + description + price + stock")
    parser.add_argument("--reject-missing", action="store_true",
                        help="Auto-reject products with unpublishable content")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be done without writing anything")

    args = parser.parse_args()

    if not (args.approve_complete or args.reject_missing):
        print("Error: Specify --approve-complete and/or --reject-missing", file=sys.stderr)
        return 1

    with db_session() as conn:
        if args.dry_run:
            print("(Dry run - no changes will be made)")

        if args.approve_complete:
            print("Auto-approving complete products...")
            count = auto_approve_complete_products(conn, args.supplier, args.limit, args.dry_run)
            print(f"  Approved: {count}")

        if args.reject_missing:
            print("Auto-rejecting products with unpublishable content...")
            count = auto_reject_missing_critical(conn, args.supplier, args.limit, args.dry_run)
            print(f"  Rejected: {count}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
