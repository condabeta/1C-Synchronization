#!/usr/bin/env python
"""Import LED Crystal price XLS into staging DB."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pymysql

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from staging.config import CRYSTAL_XLS_DEFAULT
from staging.db import db_session, fetch_one
from staging.importers.crystal import import_crystal_xls, iter_crystal_rows

DEFAULT_PATH = Path(CRYSTAL_XLS_DEFAULT)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import LED Crystal XLS to staging DB")
    parser.add_argument("--file", type=Path, default=DEFAULT_PATH, help="Path to Crystal XLS")
    parser.add_argument("--limit", type=int, default=None, help="Import only first N products")
    parser.add_argument("--skip-rows", type=int, default=0, help="Skip first N product rows")
    parser.add_argument(
        "--fetch-images",
        action="store_true",
        help="Fetch images/descriptions from led-crystal.ru (slow, site may be unavailable)",
    )
    parser.add_argument(
        "--image-limit",
        type=int,
        default=None,
        help="When --fetch-images is set, fetch for at most N products",
    )
    parser.add_argument("--dry-run", action="store_true", help="Parse only, no DB writes")
    parser.add_argument("--store-raw", action="store_true", help="Store full row in raw_data_json")
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
        sys.stderr.reconfigure(encoding="utf-8")

    args = parse_args()

    if args.dry_run:
        products = list(
            iter_crystal_rows(
                args.file,
                fetch_images=args.fetch_images,
                image_limit=args.image_limit,
                progress=print if args.fetch_images else None,
            )
        )
        with_images = sum(1 for item in products if item.get("images_json"))
        sample = products[0] if products else None
        print("Dry run finished.")
        print(f"  File:     {args.file}")
        print(f"  Products: {len(products)}")
        print(f"  With images: {with_images}")
        if sample:
            print(
                f"  Sample:   {sample['supplier_sku']} | {sample['name'][:60]} | "
                f"price={sample['price']} | images={len(sample.get('images_json') or [])}"
            )
        return 0

    try:
        print(f"Starting Crystal import from:\n  {args.file}", flush=True)
        if args.fetch_images:
            print("  Site images: enabled (may take a while)", flush=True)

        with db_session() as conn:
            stats = import_crystal_xls(
                conn,
                args.file,
                limit=args.limit,
                skip_rows=args.skip_rows,
                fetch_images=args.fetch_images,
                image_limit=args.image_limit,
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
                WHERE supplier_id = (SELECT id FROM suppliers WHERE code = 'crystal')
                """,
            )
            with_photos = fetch_one(
                conn,
                """
                SELECT COUNT(*) AS cnt FROM supplier_products
                WHERE supplier_id = (SELECT id FROM suppliers WHERE code = 'crystal')
                  AND JSON_LENGTH(images_json) > 0
                """,
            )
            sample = fetch_one(
                conn,
                """
                SELECT supplier_sku, name, price, images_json
                FROM supplier_products
                WHERE supplier_id = (SELECT id FROM suppliers WHERE code = 'crystal')
                ORDER BY id DESC LIMIT 1
                """,
            )

        print("Import finished.")
        print(f"  Run ID:       {run['id'] if run else '?'}")
        print(f"  Status:       {run['status'] if run else '?'}")
        print(f"  Total:        {stats.rows_total}")
        print(f"  Imported:     {stats.rows_imported} (new)")
        print(f"  Updated:      {stats.rows_updated}")
        print(f"  Unchanged:    {stats.rows_skipped}")
        print(f"  Errors:       {stats.rows_errors}")
        print(f"  Crystal rows: {total['cnt'] if total else 0}")
        print(f"  With images:  {with_photos['cnt'] if with_photos else 0}")
        if sample:
            imgs = len(json.loads(sample["images_json"] or "[]"))
            print(
                f"  Sample:       {sample['supplier_sku']} | {sample['name'][:50]} | "
                f"price={sample['price']} | images={imgs}"
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
