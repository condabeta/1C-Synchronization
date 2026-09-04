#!/usr/bin/env python
"""Import SWG YML feed into import_runs + supplier_products."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pymysql

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from staging.db import db_session, fetch_one
from staging.importers.swg import DEFAULT_YML_PATH, DEFAULT_YML_URL, fetch_yml, import_swg_yml, parse_swg_yml


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import SWG YML feed to staging DB")
    parser.add_argument("--url", default=DEFAULT_YML_URL, help="SWG YML export URL")
    parser.add_argument("--yml-path", default=DEFAULT_YML_PATH, help="SWG YML local file path")
    parser.add_argument("--limit", type=int, default=None, help="Import only first N offers")
    parser.add_argument("--dry-run", action="store_true", help="Parse YML only, no DB writes")
    parser.add_argument("--store-raw", action="store_true", help="Store raw offer payload in DB")
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    args = parse_args()

    source = args.yml_path if args.yml_path else args.url
    
    if args.dry_run:
        print(f"Parsing SWG YML from:\n  {source}", flush=True)
        if args.yml_path:
            xml_bytes = Path(args.yml_path).read_bytes()
        else:
            xml_bytes = fetch_yml(args.url)
        categories, products = parse_swg_yml(xml_bytes, limit=args.limit, store_raw=args.store_raw)
        print("Dry run finished.")
        print(f"  Categories: {len(categories):,}")
        print(f"  Offers:     {len(products):,}")
        if products:
            sample = products[0]
            print(
                f"  Sample:     {sample['supplier_sku']} | "
                f"price={sample['price']} | images={len(sample['images_json'])}"
            )
        return 0

    try:
        print(f"Starting SWG import from:\n  {source}", flush=True)
        print("  Expected: ~3,361 offers (usually 1-3 minutes)", flush=True)

        with db_session() as conn:
            stats = import_swg_yml(
                conn,
                url=None if args.yml_path else args.url,
                yml_path=args.yml_path,
                limit=args.limit,
                progress=print,
                store_raw=args.store_raw,
            )
            run = fetch_one(
                conn,
                """
                SELECT id, status, rows_total, rows_imported, rows_updated,
                       rows_skipped, rows_errors, started_at, finished_at
                FROM import_runs ORDER BY id DESC LIMIT 1
                """,
            )
            total = fetch_one(
                conn,
                """
                SELECT COUNT(*) AS cnt FROM supplier_products
                WHERE supplier_id = (SELECT id FROM suppliers WHERE code = 'swg')
                """,
            )
            sample = fetch_one(
                conn,
                """
                SELECT supplier_sku, name, price_retail, is_available
                FROM supplier_products
                WHERE supplier_id = (SELECT id FROM suppliers WHERE code = 'swg')
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
        print(f"  SWG rows in DB: {total['cnt'] if total else 0}")
        if sample:
            print(
                f"  Sample:    {sample['supplier_sku']} | {sample['name'][:60]} | "
                f"price={sample['price_retail']} | available={sample['is_available']}"
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
