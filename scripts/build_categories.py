#!/usr/bin/env python
"""Build the site's category tree and put every product in it.

    python scripts/build_categories.py --dry-run   # classify only, write nothing
    python scripts/build_categories.py             # build, map and assign
    python scripts/build_categories.py --unmapped  # sections no rule claims

Safe to re-run: the tree is upserted by slug, the mapping by (supplier,
section), and products take the category of their primary supplier's row.
Correcting a rule and running it again is the way to fix a wrong category.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pymysql

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from staging.categories import (
    SECTIONS_SQL,
    TAXONOMY,
    assign_products,
    build_tree,
    classify,
    coverage,
    map_sections,
)
from staging.db import db_session, fetch_all, fetch_one


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the category tree and map suppliers into it")
    parser.add_argument("--dry-run", action="store_true", help="Classify and report, write nothing")
    parser.add_argument("--unmapped", action="store_true", help="List the sections no rule claims")
    parser.add_argument("--samples", type=int, default=20, help="How many unmapped sections to print")
    return parser.parse_args()


def dry_run(conn, samples: int) -> int:
    rows = fetch_all(conn, SECTIONS_SQL)
    by_slug: dict[str, int] = {}
    unmapped: list[dict] = []
    for row in rows:
        slug, _ = classify(row["section"], row["sample_name"])
        if slug:
            by_slug[slug] = by_slug.get(slug, 0) + row["products"]
        else:
            unmapped.append(row)

    total = sum(row["products"] for row in rows)
    covered = sum(by_slug.values())
    names = {slug: name for slug, name, _ in TAXONOMY}
    print(f"Разделов поставщиков: {len(rows)} | товаров: {total:,}")
    print(f"Разложено по дереву: {covered:,} ({covered / total:.1%}) | без категории: {total - covered:,}")
    for slug, count in sorted(by_slug.items(), key=lambda item: -item[1]):
        print(f"    {names.get(slug, slug):<34} {count:>8,}")
    if unmapped:
        print(f"\nБез правила ({len(unmapped)} разделов):")
        for row in sorted(unmapped, key=lambda item: -item["products"])[:samples]:
            print(f"    {row['supplier_code']:<9} {row['products']:>7,}  {row['section'][:60]}")
    print("\nDry run - ничего не записано.")
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
        sys.stderr.reconfigure(encoding="utf-8")

    args = parse_args()
    try:
        with db_session() as conn:
            if args.dry_run:
                return dry_run(conn, args.samples)

            slugs = build_tree(conn)
            print(f"Дерево категорий: {len(slugs)} разделов")

            result = map_sections(conn, slugs)
            print(
                f"Разделы поставщиков: {result.sections} | сопоставлено {result.mapped} | "
                f"товаров покрыто {result.products_covered:,} | без категории {result.products_unmapped:,}"
            )

            names = {slug: name for slug, name, _ in TAXONOMY}
            for slug, count in result.by_category:
                print(f"    {names.get(slug, slug):<34} {count:>8,}")

            if args.unmapped and result.unmapped:
                print(f"\nБез правила ({len(result.unmapped)} разделов):")
                for row in result.unmapped[: args.samples]:
                    print(f"    {row['supplier_code']:<9} {row['products']:>7,}  {row['section'][:60]}")

            changed = assign_products(conn)
            print(f"\nПроставлена категория у товаров: {changed:,}")

            left = fetch_one(conn, "SELECT COUNT(*) AS cnt FROM products WHERE category_id IS NULL")
            print(f"Товаров всё ещё без категории: {left['cnt']:,}")

            print("\nТовары по разделам:")
            for row in coverage(conn):
                label = f"{row['parent']} / {row['category']}" if row["parent"] else row["category"]
                print(f"    {label:<52} {row['products']:>8,}")
        return 0
    except pymysql.err.OperationalError as exc:
        print("Не удалось подключиться к MySQL.", file=sys.stderr)
        print(f"  {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
