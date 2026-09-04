#!/usr/bin/env python
"""Rebuild product <-> supplier_products links broken by raw-row deletes.

Deleting rows from supplier_products used to cascade product_supplier_links away
(and NULL out product_supplier_offers.supplier_product_id), leaving products with
no traceable source row. The foreign key is fixed in
database/fix_integrity_2026_09.sql; this script repairs the data left behind.

Products are matched back to their raw row by internal_sku, which the moderation
service builds as "{supplier_code}:{supplier_sku}".

Run with --dry-run first to see what would change.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from staging.db import db_session, fetch_one

BATCH_SIZE = 5000

# products.internal_sku is "{supplier_code}:{supplier_sku}". Slicing the code off
# lets the joins below hit uq_supplier_products (supplier_id, supplier_sku)
# instead of scanning; CONCAT() on the other side would not be indexable.
SKU_FROM_INTERNAL = "SUBSTRING(p.internal_sku, CHAR_LENGTH(s.code) + 2)"

COUNT_UNLINKED_RAW = """
    SELECT COUNT(*) AS c
    FROM supplier_products sp
    JOIN suppliers s ON s.id = sp.supplier_id
    JOIN products p ON p.internal_sku = CONCAT(s.code, ':', sp.supplier_sku)
    WHERE sp.product_id IS NULL
"""

COUNT_MISSING_LINKS = f"""
    SELECT COUNT(*) AS c
    FROM products p
    JOIN suppliers s ON s.id = p.primary_supplier_id
    JOIN supplier_products sp
      ON sp.supplier_id = s.id AND sp.supplier_sku = {SKU_FROM_INTERNAL}
    WHERE NOT EXISTS (
        SELECT 1 FROM product_supplier_links l WHERE l.product_id = p.id
    )
"""

COUNT_ORPHAN_OFFERS = """
    SELECT COUNT(*) AS c
    FROM product_supplier_offers o
    JOIN supplier_products sp
      ON sp.supplier_id = o.supplier_id AND sp.supplier_sku = o.supplier_sku
    WHERE o.supplier_product_id IS NULL
"""

# MySQL does not accept LIMIT on a multi-table UPDATE, so each step selects a
# batch of ids in a derived table and updates by primary key.
RELINK_RAW_ROWS = f"""
    UPDATE supplier_products sp
    JOIN (
        SELECT sp2.id AS raw_id, p.id AS product_id
        FROM supplier_products sp2
        JOIN suppliers s ON s.id = sp2.supplier_id
        JOIN products p ON p.internal_sku = CONCAT(s.code, ':', sp2.supplier_sku)
        WHERE sp2.product_id IS NULL
        LIMIT {BATCH_SIZE}
    ) t ON t.raw_id = sp.id
    SET sp.product_id = t.product_id,
        sp.match_status = 'matched',
        sp.is_new = 0
"""

RECREATE_LINKS = f"""
    INSERT INTO product_supplier_links
        (product_id, supplier_id, supplier_product_id, supplier_sku, link_type)
    SELECT p.id, sp.supplier_id, sp.id, sp.supplier_sku, 'primary'
    FROM products p
    JOIN suppliers s ON s.id = p.primary_supplier_id
    JOIN supplier_products sp
      ON sp.supplier_id = s.id AND sp.supplier_sku = {SKU_FROM_INTERNAL}
    WHERE NOT EXISTS (
        SELECT 1 FROM product_supplier_links l WHERE l.product_id = p.id
    )
    LIMIT {BATCH_SIZE}
"""

REATTACH_OFFERS = f"""
    UPDATE product_supplier_offers o
    JOIN (
        SELECT o2.id AS offer_id, sp.id AS raw_id
        FROM product_supplier_offers o2
        JOIN supplier_products sp
          ON sp.supplier_id = o2.supplier_id AND sp.supplier_sku = o2.supplier_sku
        WHERE o2.supplier_product_id IS NULL
        LIMIT {BATCH_SIZE}
    ) t ON t.offer_id = o.id
    SET o.supplier_product_id = t.raw_id
"""


def _count(conn, sql: str) -> int:
    row = fetch_one(conn, sql)
    return int(row["c"]) if row else 0


def _report(conn, when: str) -> None:
    print(f"{when}:")
    print(f"  raw rows with no product_id (matchable): {_count(conn, COUNT_UNLINKED_RAW)}")
    print(f"  products with no supplier link:          {_count(conn, COUNT_MISSING_LINKS)}")
    print(f"  offers with no supplier_product_id:      {_count(conn, COUNT_ORPHAN_OFFERS)}")


def _run_batched(conn, label: str, sql: str, dry_run: bool) -> int:
    """Repeat a batched statement until it stops affecting rows."""
    total = 0
    while True:
        with conn.cursor() as cur:
            affected = cur.execute(sql)
        if not affected:
            break
        total += affected
        if dry_run:
            conn.rollback()
            print(f"  {label}: {affected} rows in first batch (dry run, stopping)")
            return affected
        conn.commit()
        print(f"  {label}: {total}")
    return total


def repair(conn, dry_run: bool) -> None:
    _report(conn, "Before")
    print()

    print("Relinking supplier_products.product_id...")
    _run_batched(conn, "raw rows", RELINK_RAW_ROWS, dry_run)

    print("Recreating product_supplier_links...")
    _run_batched(conn, "links", RECREATE_LINKS, dry_run)

    print("Reattaching product_supplier_offers...")
    _run_batched(conn, "offers", REATTACH_OFFERS, dry_run)

    print()
    _report(conn, "After")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="report the first batch of each step without committing")
    args = parser.parse_args()

    with db_session() as conn:
        repair(conn, args.dry_run)
        if args.dry_run:
            conn.rollback()
            print("\nDry run - nothing committed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
