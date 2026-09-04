#!/usr/bin/env python
"""Import Jazzway stock XLSX into import_runs + supplier_products."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pymysql

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from staging.config import JAZZWAY_XLSX_DEFAULT
from staging.db import db_session, fetch_one
from staging.importers.jazzway import import_jazzway_xlsx, normalize_row
import pandas as pd
from staging.importers.jazzway import HEADER_ROW

DEFAULT_PATH = Path(JAZZWAY_XLSX_DEFAULT)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import Jazzway XLSX to staging DB")
    parser.add_argument("--file", type=Path, default=DEFAULT_PATH, help="Path to Jazzway XLSX")
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
        df = pd.read_excel(args.file, header=HEADER_ROW)
        ok = 0
        sample = None
        for _, row in df.iterrows():
            item = normalize_row(row.to_dict())
            if item:
                ok += 1
                sample = item
        print("Dry run finished.")
        print(f"  File:    {args.file}")
        print(f"  Products: {ok}")
        if sample:
            print(
                f"  Sample:  {sample['supplier_sku']} | price={sample['price_retail']} | "
                f"stock={sample['stock_qty']}"
            )
        return 0

    try:
        print(f"Starting Jazzway import from:\n  {args.file}", flush=True)
        print("  Expected: ~2,845 products (usually 1-2 minutes)", flush=True)

        with db_session() as conn:
            stats = import_jazzway_xlsx(
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
                       rows_skipped, rows_errors
                FROM import_runs ORDER BY id DESC LIMIT 1
                """,
            )
            total = fetch_one(
                conn,
                """
                SELECT COUNT(*) AS cnt FROM supplier_products
                WHERE supplier_id = (SELECT id FROM suppliers WHERE code = 'jazzway')
                """,
            )
            sample = fetch_one(
                conn,
                """
                SELECT supplier_sku, name, price_retail, stock_qty
                FROM supplier_products
                WHERE supplier_id = (SELECT id FROM suppliers WHERE code = 'jazzway')
                ORDER BY id DESC LIMIT 1
                """,
            )

        print("Import finished.")
        print(f"  Run ID:    {run['id'] if run else '?'}")
        print(f"  Status:    {run['status'] if run else '?'}")
        print(f"  Total:     {stats.rows_total}")
        print(f"  Imported:  {stats.rows_imported} (new)")
        print(f"  Updated:   {stats.rows_updated}")
        print(f"  Unchanged: {stats.rows_skipped}")
        print(f"  Errors:    {stats.rows_errors}")
        print(f"  Jazzway rows in DB: {total['cnt'] if total else 0}")
        if sample:
            print(
                f"  Sample:    {sample['supplier_sku']} | {sample['name'][:60]} | "
                f"price={sample['price_retail']} | stock={sample['stock_qty']}"
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
