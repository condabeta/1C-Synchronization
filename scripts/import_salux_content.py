#!/usr/bin/env python
"""Fill Salux products with photos and descriptions from stz-salux.ru.

Salux exports neither, and their site is the only source they have. The site
publishes one page per series, so every article of a series gets that series'
description and photo set - which is what the series is: the same luminaire in
different wattages.

    python scripts/import_salux_content.py --dry-run   # crawl and match only
    python scripts/import_salux_content.py             # write to the DB

Only the price rows are touched. Content flows on to the catalogue through
moderation as usual.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import pymysql

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from staging.db import db_session, fetch_all
from staging.importers.salux_site import (
    ATTRIBUTION,
    DEFAULT_DELAY,
    SaluxPage,
    build_index,
    crawl,
    lookup,
)

SUPPLIER_CODE = "salux"

SELECT_SQL = """
    SELECT sp.id, sp.supplier_sku, sp.name, sp.description, sp.images_json,
           sp.attributes_json->>'$.sheet' AS sheet
    FROM supplier_products sp
    JOIN suppliers s ON s.id = sp.supplier_id
    WHERE s.code = %s
"""

# supplier_products has no updated_at, and last_seen_at means "was in the last
# price list", which a content crawl does not change.
# The next price import will not undo this: the shared upsert keeps the existing
# description and images when the incoming price row has none, which is always
# the case for Salux.
UPDATE_SQL = """
    UPDATE supplier_products
    SET description = %s, images_json = %s, product_url = %s
    WHERE id = %s
"""

ATTRIBUTION_SQL = """
    UPDATE suppliers SET content_attribution = %s WHERE code = %s AND
      (content_attribution IS NULL OR content_attribution <> %s)
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape Salux content into the staging DB")
    parser.add_argument("--dry-run", action="store_true", help="Crawl and match only, write nothing")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY, help="Pause between requests")
    parser.add_argument("--cache", type=Path, help="Read/write the crawl as JSON instead of refetching")
    parser.add_argument("--quiet", action="store_true", help="Do not print each page as it is fetched")
    return parser.parse_args()


def load_pages(args: argparse.Namespace) -> list[SaluxPage]:
    if args.cache and args.cache.exists():
        raw = json.loads(args.cache.read_text(encoding="utf-8"))
        print(f"Страницы из кэша: {args.cache} ({len(raw)})")
        return [SaluxPage(**item) for item in raw]

    print("Обход каталога stz-salux.ru ...")
    pages = crawl(delay=args.delay, progress=None if args.quiet else print)
    if args.cache:
        args.cache.write_text(
            json.dumps([page.__dict__ for page in pages], ensure_ascii=False),
            encoding="utf-8",
        )
    return pages


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
        sys.stderr.reconfigure(encoding="utf-8")

    args = parse_args()
    try:
        pages = load_pages(args)
        index = build_index(pages)
        print(
            f"Страниц с серией: {len(pages)} | уникальных серий: {len(index)} | "
            f"фото всего: {sum(len(page.images) for page in pages)}"
        )

        with db_session() as conn:
            rows = fetch_all(conn, SELECT_SQL, (SUPPLIER_CODE,))
            updates = []
            missing: Counter[str] = Counter()
            photos = 0
            for row in rows:
                page = lookup(row, index)
                if not page:
                    missing[str(row["name"])[:40]] += 1
                    continue
                photos += len(page.images)
                updates.append(
                    (
                        page.description,
                        json.dumps(page.images, ensure_ascii=False) if page.images else None,
                        page.url,
                        row["id"],
                    )
                )

            print(
                f"Товаров Салюкса: {len(rows)} | сопоставлено со страницей: {len(updates)} | "
                f"без страницы: {len(rows) - len(updates)} | фото проставлено: {photos:,}"
            )
            if missing:
                print("Без страницы на сайте:")
                for name, count in missing.most_common(15):
                    print(f"    {name:<42} {count}")

            if args.dry_run:
                print("\nDry run - ничего не записано.")
                return 0

            with conn.cursor() as cur:
                cur.executemany(UPDATE_SQL, updates)
                cur.execute(ATTRIBUTION_SQL, (ATTRIBUTION, SUPPLIER_CODE, ATTRIBUTION))
            conn.commit()
            print(f"Записано: {len(updates)} товаров. Указание источника: «{ATTRIBUTION}»")
        return 0
    except pymysql.err.OperationalError as exc:
        print("Не удалось подключиться к MySQL.", file=sys.stderr)
        print(f"  {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
