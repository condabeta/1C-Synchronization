#!/usr/bin/env python
"""Import ViaSvet multi-sheet XLSX + local photo folders into staging DB."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pymysql

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from staging.config import VIASVET_PHOTOS_DIR, VIASVET_XLSX_DEFAULT
from staging.db import db_session, fetch_one
from staging.importers.viasvet import import_viasvet_xlsx, iter_viasvet_products, scan_photo_folders

DEFAULT_XLSX = Path(VIASVET_XLSX_DEFAULT)
DEFAULT_PHOTOS = Path(VIASVET_PHOTOS_DIR)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import ViaSvet XLSX + photos to staging DB")
    parser.add_argument("--file", type=Path, default=DEFAULT_XLSX, help="Path to ViaSvet XLSX")
    parser.add_argument(
        "--photos",
        type=Path,
        default=DEFAULT_PHOTOS,
        help="Path to local photo folders (на сайт)",
    )
    parser.add_argument("--limit", type=int, default=None, help="Import only first N products")
    parser.add_argument("--skip-rows", type=int, default=0, help="Skip first N product rows")
    parser.add_argument(
        "--profiles-only",
        action="store_true",
        help="Import only LED profile sheets (skip tape, accessories, power supplies)",
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
            iter_viasvet_products(
                args.file,
                args.photos,
                profiles_only=args.profiles_only,
            )
        )
        photo_index = scan_photo_folders(args.photos)
        with_photos = sum(1 for p in products if p.get("images_json"))
        profiles = sum(1 for p in products if "Профил" in (p.get("supplier_category") or ""))
        sample = products[0] if products else None

        print("Dry run finished.")
        print(f"  File:     {args.file}")
        print(f"  Photos:   {args.photos} ({len(photo_index)} folders)")
        print(f"  Products: {len(products)}")
        print(f"  Profiles: {profiles}")
        print(f"  With photos: {with_photos}")
        if sample:
            imgs = len(sample.get("images_json") or [])
            print(
                f"  Sample:   {sample['supplier_sku']} | price={sample['price']} | "
                f"rrc={sample['price_retail']} | images={imgs}"
            )
        return 0

    try:
        scope = "profiles only" if args.profiles_only else "all product sheets"
        print(f"Starting ViaSvet import ({scope}) from:\n  {args.file}\n  {args.photos}", flush=True)

        with db_session() as conn:
            stats = import_viasvet_xlsx(
                conn,
                args.file,
                args.photos,
                limit=args.limit,
                skip_rows=args.skip_rows,
                profiles_only=args.profiles_only,
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
                WHERE supplier_id = (SELECT id FROM suppliers WHERE code = 'viasvet')
                """,
            )
            with_photos = fetch_one(
                conn,
                """
                SELECT COUNT(*) AS cnt FROM supplier_products
                WHERE supplier_id = (SELECT id FROM suppliers WHERE code = 'viasvet')
                  AND JSON_LENGTH(images_json) > 0
                """,
            )
            sample = fetch_one(
                conn,
                """
                SELECT supplier_sku, name, price, price_retail, images_json
                FROM supplier_products
                WHERE supplier_id = (SELECT id FROM suppliers WHERE code = 'viasvet')
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
        print(f"  ViaSvet rows: {total['cnt'] if total else 0}")
        print(f"  With photos:  {with_photos['cnt'] if with_photos else 0}")
        if sample:
            import json

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
