#!/usr/bin/env python
"""Data-quality and integrity report for every supplier.

Replaces the ad-hoc check_*/calculate_*/reanalyze_* scripts that used to sit in
the project root, each answering one question about one supplier.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from staging.db import db_session, fetch_all

COVERAGE_SQL = """
    SELECT s.code,
           COUNT(*)                                                        AS total,
           COALESCE(SUM(sp.description IS NOT NULL AND sp.description <> ''), 0) AS descriptions,
           COALESCE(SUM(sp.images_json IS NOT NULL AND JSON_LENGTH(sp.images_json) > 0), 0) AS images,
           COALESCE(SUM(sp.price > 0), 0)                                  AS prices,
           COALESCE(SUM(sp.stock_qty > 0), 0)                              AS in_stock,
           COALESCE(SUM(sp.product_id IS NULL), 0)                         AS not_in_catalogue
    FROM supplier_products sp
    JOIN suppliers s ON s.id = sp.supplier_id
    GROUP BY s.code
    ORDER BY total DESC
"""

MODERATION_SQL = """
    SELECT COALESCE(s.code, '(none)') AS code, p.status, COUNT(*) AS n
    FROM products p
    LEFT JOIN suppliers s ON s.id = p.primary_supplier_id
    GROUP BY 1, 2
    ORDER BY n DESC
"""

QUEUE_SQL = """
    SELECT queue_reason, status, COUNT(*) AS n
    FROM moderation_queue
    GROUP BY 1, 2
    ORDER BY n DESC
"""

INTEGRITY_SQL = """
    SELECT
      (SELECT COUNT(*) FROM products p
         WHERE NOT EXISTS (SELECT 1 FROM product_supplier_links l WHERE l.product_id = p.id))
        AS products_without_source,
      (SELECT COUNT(*) FROM product_supplier_offers WHERE supplier_product_id IS NULL)
        AS offers_without_source,
      (SELECT COUNT(*) FROM product_images WHERE stored_path IS NULL)
        AS images_not_downloaded,
      (SELECT COUNT(*) FROM sync_outbox WHERE status = 'pending')
        AS sync_pending
"""

ERRORS_SQL = """
    SELECT LEFT(error_message, 70) AS error_message, COUNT(*) AS n
    FROM import_run_errors
    GROUP BY 1
    ORDER BY n DESC
    LIMIT 5
"""


def _pct(part: int | None, whole: int) -> str:
    if not whole:
        return "-"
    return f"{(part or 0) * 100 / whole:5.1f}%"


def main() -> int:
    with db_session() as conn:
        print("=== SUPPLIER DATA COVERAGE ===")
        print(f"{'supplier':10} {'rows':>8} {'descr':>16} {'images':>16} "
              f"{'prices':>16} {'in stock':>16} {'unqueued':>9}")
        for r in fetch_all(conn, COVERAGE_SQL):
            total = r["total"]
            print(f"{r['code']:10} {total:>8} "
                  f"{r['descriptions']:>8} {_pct(r['descriptions'], total)} "
                  f"{r['images']:>8} {_pct(r['images'], total)} "
                  f"{r['prices']:>8} {_pct(r['prices'], total)} "
                  f"{r['in_stock']:>8} {_pct(r['in_stock'], total)} "
                  f"{r['not_in_catalogue']:>9}")

        print("\n=== CATALOGUE BY STATUS ===")
        for r in fetch_all(conn, MODERATION_SQL):
            print(f"  {r['code']:10} {r['status']:20} {r['n']:>8}")

        print("\n=== MODERATION QUEUE ===")
        for r in fetch_all(conn, QUEUE_SQL):
            print(f"  {r['queue_reason']:22} {r['status']:10} {r['n']:>8}")

        print("\n=== INTEGRITY ===")
        for key, value in fetch_all(conn, INTEGRITY_SQL)[0].items():
            print(f"  {key:26} {value:>8}")

        errors = fetch_all(conn, ERRORS_SQL)
        if errors:
            print("\n=== TOP IMPORT ERRORS ===")
            for r in errors:
                print(f"  {r['n']:>8}  {r['error_message']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
