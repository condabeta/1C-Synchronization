#!/usr/bin/env python
"""Load Svetoyar's own articles and product names into the staging DB.

The source is Svetoyar's own price list for Salux-made goods - our article, our
name, the Salux order marking that links them. It is not a supplier feed, so it
lands in `own_articles` rather than `supplier_products`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pymysql

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from staging.config import OWN_PRICE_XLSX_DEFAULT
from staging.db import db_session
from staging.own_articles import iter_own_articles, load_own_articles, match_report

DEFAULT_PATH = Path(OWN_PRICE_XLSX_DEFAULT)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load Svetoyar's own articles")
    parser.add_argument("--file", type=Path, default=DEFAULT_PATH, help="Path to our price list")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, no DB writes")
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
        sys.stderr.reconfigure(encoding="utf-8")

    args = parse_args()

    if args.dry_run:
        rows = list(iter_own_articles(args.file, progress=print))
        with_marking = sum(1 for r in rows if r["supplier_marking"])
        with_name = sum(1 for r in rows if r["own_name"])
        print("Dry run finished.")
        print(f"  Файл:            {args.file}")
        print(f"  Артикулов:       {len(rows)}")
        print(f"  С маркировкой:   {with_marking}")
        print(f"  С наименованием: {with_name}")
        if rows:
            s = rows[0]
            print(f"  Пример:          {s['own_sku']} | {s['own_name']} | {s['supplier_marking']}")
        return 0

    try:
        print(f"Загружаем свои артикулы из:\n  {args.file}", flush=True)
        with db_session() as conn:
            count = load_own_articles(conn, args.file, progress=print)
            report = match_report(conn)

        print("Готово.")
        print(f"  Загружено артикулов:            {count:,}")
        print(f"  Из них сопоставлено с прайсом:  {report['own_matched']:,}")
        print(
            f"  Позиций поставщика покрыто:     {report['supplier_covered']:,} "
            f"из {report['supplier_total']:,}"
        )
        return 0
    except pymysql.err.OperationalError as exc:
        print("Не удалось подключиться к MySQL.", file=sys.stderr)
        print(f"  {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Загрузка не удалась: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
