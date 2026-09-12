#!/usr/bin/env python
"""Import the Svet NN price list into the staging DB."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

import pymysql

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from staging.config import SVETNN_XLSX_DEFAULT
from staging.db import db_session, fetch_one
from staging.importers.svetnn import import_svetnn_xlsx, iter_svetnn_rows

DEFAULT_PATH = Path(SVETNN_XLSX_DEFAULT)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import Svet NN price list to staging DB")
    parser.add_argument("--file", type=Path, default=DEFAULT_PATH, help="Path to Svet NN workbook")
    parser.add_argument("--limit", type=int, default=None, help="Import only first N products")
    parser.add_argument("--skip-rows", type=int, default=0, help="Skip first N product rows")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, no DB writes")
    parser.add_argument("--store-raw", action="store_true", help="Store full row in raw_data_json")
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    args = parse_args()

    if args.dry_run:
        products = list(iter_svetnn_rows(args.file, progress=print))
        by_category = Counter(item["supplier_category"] for item in products)
        with_marking = sum(1 for item in products if item["manufacturer_code"])
        print("Dry run finished.")
        print(f"  File:      {args.file}")
        print(f"  Products:  {len(products)} ({len({p['supplier_sku'] for p in products})} unique)")
        print(f"  With Salux marking: {with_marking}")
        for category, count in by_category.most_common():
            print(f"    {str(category)[:50]:<50} {count}")
        if products:
            sample = products[0]
            print(
                f"  Sample:    {sample['supplier_sku']} | {str(sample['name'])[:30]} | "
                f"dealer={sample['price']}"
            )
        return 0

    try:
        print(f"Starting Svet NN import from:\n  {args.file}", flush=True)

        with db_session() as conn:
            stats = import_svetnn_xlsx(
                conn,
                args.file,
                limit=args.limit,
                skip_rows=args.skip_rows,
                progress=print,
                store_raw=args.store_raw,
            )
            run = fetch_one(conn, "SELECT id, status FROM import_runs ORDER BY id DESC LIMIT 1")
            total = fetch_one(
                conn,
                """
                SELECT COUNT(*) AS cnt FROM supplier_products
                WHERE supplier_id = (SELECT id FROM suppliers WHERE code = 'svetnn')
                """,
            )
            sample = fetch_one(
                conn,
                """
                SELECT supplier_sku, name, price, price_retail
                FROM supplier_products
                WHERE supplier_id = (SELECT id FROM suppliers WHERE code = 'svetnn')
                ORDER BY id DESC LIMIT 1
                """,
            )

        print("Import finished.")
        print(f"  Run ID:      {run['id'] if run else '?'}")
        print(f"  Status:      {run['status'] if run else '?'}")
        print(f"  Total:       {stats.rows_total}")
        print(f"  Imported:    {stats.rows_imported} (new)")
        print(f"  Updated:     {stats.rows_updated}")
        print(f"  Unchanged:   {stats.rows_skipped}")
        print(f"  Errors:      {stats.rows_errors}")
        print(f"  Svet NN rows: {total['cnt'] if total else 0}")
        if sample:
            print(
                f"  Sample:      {sample['supplier_sku']} | dealer={sample['price']} | "
                f"retail={sample['price_retail']}"
            )
        return 0
    except pymysql.err.OperationalError as exc:
        print("Could not connect to MySQL.", file=sys.stderr)
        print(f"  {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Import failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
