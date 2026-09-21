#!/usr/bin/env python
"""Give the catalogue what publishing needs, and write the 1C exchange files.

Two steps that have to happen before anything can be sent anywhere, and both
can be done now, without the site or the 1C tariff:

    python scripts/prepare_publishing.py            # slugs, GUIDs, meta titles
    python scripts/prepare_publishing.py --export   # and write import.xml/offers.xml
    python scripts/prepare_publishing.py --export --out D:\\exchange --limit 500

A slug is a product's URL and a GUID is how 1C recognises it. Both must be
stable: a changed slug is a dead link, a changed GUID makes 1C create a second
copy of the product. They are therefore generated once and stored, derived from
the internal article so that even a rebuilt database produces the same values.

The export writes only what moderation has approved. Today that is one product,
so the files will be nearly empty until the queue has been worked through -
that is expected, and the run says so.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pymysql

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from staging.db import db_session, fetch_one
from staging.publishing import commerceml
from staging.publishing.slugs import coverage, fill_categories, fill_products

DEFAULT_OUT = PROJECT_ROOT / "exchange"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare the catalogue for publishing")
    parser.add_argument("--export", action="store_true", help="Also write the CommerceML files")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Where to write them")
    parser.add_argument("--limit", type=int, default=None, help="Export only the first N products")
    parser.add_argument("--skip-fill", action="store_true", help="Export without filling slugs first")
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
        sys.stderr.reconfigure(encoding="utf-8")

    args = parse_args()
    try:
        with db_session() as conn:
            if not args.skip_fill:
                categories = fill_categories(conn)
                print(f"Категории: заполнено {categories}")
                stats = fill_products(conn, progress=print)
                print(
                    f"Товары: слагов {stats['slugs']:,} | GUID {stats['guids']:,} | "
                    f"meta-заголовков {stats['titles']:,}"
                )

            state = coverage(conn)
            print(
                f"\nГотовность: товаров {state['products']:,} | с адресом {state['with_slug']:,} | "
                f"с GUID {state['with_guid']:,} | с meta {state['with_meta']:,} | "
                f"со ставкой НДС {state['with_vat']:,}"
            )
            if not state["with_vat"]:
                print("  НДС не задан ни у одного товара - ставку должен назвать клиент.")

            approved = fetch_one(
                conn,
                "SELECT COUNT(*) AS cnt FROM products WHERE status IN %s"
                % (commerceml.PUBLISHABLE_STATUSES,),
            )
            print(f"  Прошло модерацию и готово к выгрузке: {approved['cnt']:,}")

            if args.export:
                result = commerceml.export(conn, args.out, limit=args.limit)
                print(
                    f"\nВыгрузка CommerceML: разделов {result.categories} | "
                    f"товаров {result.products:,} | предложений {result.offers:,}"
                )
                if result.skipped_no_category:
                    print(f"  без раздела: {result.skipped_no_category:,}")
                if result.skipped_no_price:
                    print(f"  без цены (в предложения не попали): {result.skipped_no_price:,}")
                for path in result.files:
                    print(f"  {path}  ({path.stat().st_size:,} байт)")
        return 0
    except pymysql.err.OperationalError as exc:
        print("Не удалось подключиться к MySQL.", file=sys.stderr)
        print(f"  {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
