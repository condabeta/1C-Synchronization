#!/usr/bin/env python
"""Import the Точка Зрения price list.

    python scripts/import_tochka_zreniya.py --dry-run
    python scripts/import_tochka_zreniya.py

The file is loaded twice over, into two tables, because it is two things at
once. It is a supplier price list - name, price, availability, link - and it is
also the record of our own articles and names for those goods, the way
светнн1.xlsx is for Salux. Анна keeps both by hand.

Their articles collide with Salux's and SWG's, which is why this exists at all:
until Анна wrote on 29.09.2026 we were reading «Светодиодный проектор Premier
MINI» as a Salux luminaire, because both are article 000001.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

import pymysql

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from staging.db import db_session, fetch_one
from staging.importers.common import (
    ImportStats,
    finish_import_run,
    flush_product_batch,
    get_supplier_and_source,
    start_import_run,
)
from staging.importers.tochka_zreniya import (
    PRICE_FILE,
    SITE_MANUFACTURER,
    SOURCE_CODE,
    SUPPLIER_CODE,
    iter_own_article_rows,
    iter_tochka_zreniya_rows,
)
from staging.own_articles import save_own_articles

BATCH_SIZE = 500


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import the Точка Зрения price list")
    parser.add_argument("--file", type=Path, default=PRICE_FILE)
    parser.add_argument("--dry-run", action="store_true", help="Parse and report, write nothing")
    return parser.parse_args()


def dry_run(path: Path) -> int:
    items = list(iter_tochka_zreniya_rows(path, progress=print))
    categories = Counter(item["supplier_category"] or "(без категории)" for item in items)
    numeric = sum(1 for item in items if item["supplier_sku"].isdigit())
    print(f"\nТоваров: {len(items):,} | в наличии: {sum(i['is_available'] for i in items):,}")
    print(f"Артикулы: {numeric} числовых (000001…), {len(items) - numeric} именованных (Premier F108…)")
    print(f"Производитель на сайте: {SITE_MANUFACTURER}")
    for name, count in categories.most_common():
        print(f"   {name[:58]:<58} {count:>4}")
    print("\nDry run - ничего не записано.")
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
        sys.stderr.reconfigure(encoding="utf-8")

    args = parse_args()
    if not args.file.exists():
        print(f"Файл не найден: {args.file}", file=sys.stderr)
        return 1

    try:
        if args.dry_run:
            return dry_run(args.file)

        with db_session() as conn:
            supplier_id, source_id = get_supplier_and_source(conn, SUPPLIER_CODE, SOURCE_CODE)
            run_id = start_import_run(
                conn, supplier_id, source_id, str(args.file),
                f"{args.file.name}:{args.file.stat().st_size}",
            )
            stats = ImportStats()
            existing: dict[str, str] = {}
            batch: list[dict] = []

            try:
                for item in iter_tochka_zreniya_rows(args.file, progress=print):
                    stats.rows_total += 1
                    batch.append(item)
                    if len(batch) >= BATCH_SIZE:
                        flush_product_batch(conn, supplier_id, run_id, batch, existing, stats)
                flush_product_batch(conn, supplier_id, run_id, batch, existing, stats)
                finish_import_run(conn, run_id, stats, status="success")
            except Exception:
                conn.rollback()
                finish_import_run(conn, run_id, stats, status="failed")
                raise

            own = save_own_articles(
                conn, SUPPLIER_CODE, list(iter_own_article_rows(args.file)),
                source_file=args.file.name,
            )
            total = fetch_one(
                conn, "SELECT COUNT(*) AS cnt FROM supplier_products WHERE supplier_id = %s",
                (supplier_id,),
            )

        print("\nИмпорт завершён.")
        print(f"  Прочитано:     {stats.rows_total:,}")
        print(f"  Новых:         {stats.rows_imported:,}")
        print(f"  Обновлено:     {stats.rows_updated:,}")
        print(f"  Без изменений: {stats.rows_skipped:,}")
        print(f"  Ошибок:        {stats.rows_errors:,}")
        print(f"  Наших артикулов записано: {own:,}")
        print(f"  Всего в базе Точка Зрения: {total['cnt'] if total else 0:,}")
        return 0
    except pymysql.err.OperationalError as exc:
        print("Не удалось подключиться к MySQL.", file=sys.stderr)
        print(f"  {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
