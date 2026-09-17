#!/usr/bin/env python
"""Import Dekomo CSV into import_runs + supplier_products."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pymysql

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from staging.config import DEKOMO_CSV_DEFAULT
from staging.db import db_session, fetch_one
from staging.importers.dekomo import import_dekomo_csv


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import Dekomo CSV to staging DB")
    parser.add_argument(
        "--file",
        type=Path,
        default=Path(DEKOMO_CSV_DEFAULT),
        help="Path to Dekomo CSV file",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Import only first N data rows (for pilot testing)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse CSV only, do not write to database",
    )
    parser.add_argument(
        "--skip-rows",
        type=int,
        default=0,
        help="Skip first N data rows in CSV (use ~43000 to resume after interrupted import)",
    )
    parser.add_argument(
        "--store-raw",
        action="store_true",
        help="Store full CSV row in raw_data_json (slower, uses more disk space)",
    )
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
        sys.stderr.reconfigure(encoding="utf-8")

    args = parse_args()

    if args.dry_run:
        from staging.importers.dekomo import _normalize_row, iter_dekomo_rows

        ok = 0
        skipped = 0
        sample = None
        for row in iter_dekomo_rows(args.file, limit=args.limit):
            normalized = _normalize_row(row, store_raw=False)
            if normalized:
                ok += 1
                sample = normalized
            else:
                skipped += 1
        print("Dry run finished (no database writes).")
        print(f"  File:    {args.file}")
        print(f"  Parsed:  {ok}")
        print(f"  Skipped: {skipped}")
        if sample:
            print(f"  Sample:  {sample['supplier_sku']} | price={sample['price_retail']} | stock={sample['stock_qty']}")
        return 0

    try:
        print(f"Starting Dekomo import from:\n  {args.file}", flush=True)
        if args.limit:
            print(f"  Limit: {args.limit:,} rows", flush=True)
        else:
            print("  Limit: all rows (~151,000 — expect 5-15 minutes)", flush=True)

        with db_session() as conn:
            stats = import_dekomo_csv(
                conn,
                args.file,
                limit=args.limit,
                skip_rows=args.skip_rows,
                progress=print,
                store_raw=args.store_raw,
            )

            run = fetch_one(
                conn,
                """
                SELECT id, status, rows_total, rows_imported, rows_updated,
                       rows_skipped, rows_errors, started_at, finished_at
                FROM import_runs
                ORDER BY id DESC
                LIMIT 1
                """,
            )
            total_products = fetch_one(
                conn,
                "SELECT COUNT(*) AS cnt FROM supplier_products WHERE supplier_id = (SELECT id FROM suppliers WHERE code = 'dekomo')",
            )
            sample = fetch_one(
                conn,
                """
                SELECT supplier_sku, name, price_retail, stock_qty
                FROM supplier_products
                WHERE supplier_id = (SELECT id FROM suppliers WHERE code = 'dekomo')
                ORDER BY id DESC
                LIMIT 1
                """,
            )

        print("Import finished.")
        print(f"  File:      {args.file}")
        print(f"  Limit:     {args.limit or 'all rows'}")
        print(f"  Run ID:    {run['id'] if run else '?'}")
        print(f"  Status:    {run['status'] if run else '?'}")
        print(f"  Total:     {stats.rows_total}")
        print(f"  Imported:  {stats.rows_imported} (new)")
        print(f"  Updated:   {stats.rows_updated}")
        print(f"  Unchanged: {stats.rows_skipped}")
        print(f"  Errors:    {stats.rows_errors}")
        print(f"  Dekomo rows in DB: {total_products['cnt'] if total_products else 0}")
        if sample:
            print(
                f"  Sample:    {sample['supplier_sku']} | {sample['name'][:60]} | "
                f"price={sample['price_retail']} | stock={sample['stock_qty']}"
            )
        return 0 if stats.rows_errors == 0 or stats.rows_imported or stats.rows_updated else 1
    except pymysql.err.OperationalError as exc:
        print("Could not connect to MySQL.", file=sys.stderr)
        print(f"  {exc}", file=sys.stderr)
        print("Run: python scripts/setup_database.py", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Import failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
