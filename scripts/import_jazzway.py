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

from staging.config import JAZZWAY_XLSX_DEFAULT, JAZZWAY_YML_URL
from staging.db import db_session, fetch_one
from staging.importers.jazzway import (
    HEADER_ROW,
    content_for,
    import_jazzway_xlsx,
    normalize_row,
)
from staging.importers.jazzway_feed import load_feed_index
import pandas as pd

DEFAULT_PATH = Path(JAZZWAY_XLSX_DEFAULT)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import Jazzway XLSX to staging DB")
    parser.add_argument("--file", type=Path, default=DEFAULT_PATH, help="Path to Jazzway XLSX")
    parser.add_argument("--limit", type=int, default=None, help="Import only first N products")
    parser.add_argument("--skip-rows", type=int, default=0, help="Skip first N product rows")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, no DB writes")
    parser.add_argument("--store-raw", action="store_true", help="Store full row in raw_data_json")
    parser.add_argument(
        "--feed-url", default=JAZZWAY_YML_URL,
        help="Jazzway YML content feed (descriptions, images, specs, documents)",
    )
    parser.add_argument(
        "--feed-file", type=Path, default=None,
        help="Read the feed from a local file instead of downloading it",
    )
    parser.add_argument(
        "--no-feed", action="store_true",
        help="Import price and stock only, without feed content",
    )
    return parser.parse_args()


def load_feed(args) -> dict:
    """Load the content feed; a feed failure must not block a price update."""
    if args.no_feed:
        return {}
    source = args.feed_file or args.feed_url
    print(f"Loading Jazzway content feed:\n  {source}", flush=True)
    try:
        index = load_feed_index(url=args.feed_url, file_path=args.feed_file)
    except Exception as exc:
        print(f"  Feed unavailable ({exc}); continuing with price and stock only.",
              file=sys.stderr)
        return {}
    print(f"  {len(index):,} offers indexed.", flush=True)
    return index


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    args = parse_args()

    if args.dry_run:
        feed_index = load_feed(args)
        df = pd.read_excel(args.file, header=HEADER_ROW)
        ok = matched = with_description = 0
        images = 0
        sample = None
        for _, row in df.iterrows():
            row_dict = row.to_dict()
            content = content_for(row_dict, feed_index)
            item = normalize_row(row_dict, content=content)
            if item:
                ok += 1
                matched += bool(content)
                with_description += bool(item["description"])
                images += len(item["images_json"])
                sample = item
        print("Dry run finished.")
        print(f"  File:        {args.file}")
        print(f"  Products:    {ok}")
        print(f"  Feed match:  {matched} ({ok - matched} without feed content)")
        print(f"  Descriptions:{with_description}")
        print(f"  Images:      {images}")
        if sample:
            print(
                f"  Sample:  {sample['supplier_sku']} | price={sample['price_retail']} | "
                f"stock={sample['stock_qty']} | images={len(sample['images_json'])}"
            )
        return 0

    try:
        feed_index = load_feed(args)
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
                feed_index=feed_index,
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
