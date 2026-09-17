#!/usr/bin/env python
"""Re-apply the markup rules to prices already in the staging DB.

Imports apply the rules as they run, so this is for the other case: a
coefficient changed, or a rule was added, and the existing rows have to catch
up without waiting for the next price list.

    python scripts/recalc_prices.py --dry-run             # what would change
    python scripts/recalc_prices.py --supplier jazzway    # one supplier
    python scripts/recalc_prices.py                       # every supplier with rules
    python scripts/recalc_prices.py --supplier jazzway --list-categories

--list-categories prints the categories a supplier actually delivers together
with the rule each one hits. That is the way to check a rule matches what the
supplier really writes before trusting the prices it produces.

Rows are left with their old content_hash on purpose: the next import will see
them as changed once, rewrite them, and settle. Nothing is lost either way.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pymysql.connections import Connection

from staging.db import db_session, fetch_all
from staging.pricing import PricingRules, as_decimal, load_rules

FETCH_SQL = """
    SELECT id, supplier_sku, name, supplier_category, supplier_category_path,
           price, price_retail
    FROM supplier_products
    WHERE supplier_id = %s
"""

UPDATE_SQL = """
    UPDATE supplier_products
    SET price_retail = %s, pricing_rule_id = %s
    WHERE id = %s
"""

# Only products this supplier owns the content for. A product whose primary
# supplier is someone else keeps that supplier's price.
PROPAGATE_PRODUCTS_SQL = """
    UPDATE products p
    JOIN supplier_products sp
      ON sp.product_id = p.id AND sp.supplier_id = %s
    SET p.price_retail = sp.price_retail, p.updated_at = NOW()
    WHERE sp.price_retail IS NOT NULL
      AND (p.primary_supplier_id = sp.supplier_id OR p.primary_supplier_id IS NULL)
      AND (p.price_retail IS NULL OR p.price_retail <> sp.price_retail)
"""

PROPAGATE_OFFERS_SQL = """
    UPDATE product_supplier_offers o
    JOIN supplier_products sp
      ON sp.product_id = o.product_id AND sp.supplier_id = o.supplier_id
    SET o.price_retail = sp.price_retail
    WHERE o.supplier_id = %s
      AND sp.price_retail IS NOT NULL
      AND (o.price_retail IS NULL OR o.price_retail <> sp.price_retail)
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Recalculate retail prices from markup rules")
    parser.add_argument(
        "--supplier",
        action="append",
        dest="suppliers",
        metavar="CODE",
        help="Supplier code (repeatable). Default: every supplier that has rules.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report changes, write nothing")
    parser.add_argument(
        "--list-categories",
        action="store_true",
        help="List the supplier's categories and the rule each one matches, then exit",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=5,
        help="How many example price changes to print per supplier (default 5)",
    )
    return parser.parse_args()


def suppliers_to_process(conn: Connection, codes: list[str] | None) -> list[dict[str, Any]]:
    if codes:
        placeholders = ", ".join(["%s"] * len(codes))
        rows = fetch_all(
            conn,
            f"SELECT id, code, name FROM suppliers WHERE code IN ({placeholders}) ORDER BY code",
            codes,
        )
        found = {row["code"] for row in rows}
        for code in codes:
            if code not in found:
                print(f"  Unknown supplier code: {code}", file=sys.stderr)
        return rows

    return fetch_all(
        conn,
        """
        SELECT DISTINCT s.id, s.code, s.name
        FROM suppliers s
        JOIN supplier_pricing_rules r ON r.supplier_id = s.id AND r.is_active = 1
        ORDER BY s.code
        """,
    )


def print_rules(rules: PricingRules) -> None:
    for rule in rules.rules:
        base = "РРЦ поставщика" if rule.base_field == "price_retail" else "цена поставщика"
        print(f"    [{rule.priority:>3}] x{rule.coefficient:g} от {base:<16} {rule.describe()}")


def list_categories(conn: Connection, supplier: dict[str, Any], rules: PricingRules) -> None:
    rows = fetch_all(
        conn,
        """
        SELECT supplier_category_path, supplier_category,
               COUNT(*) AS cnt, MIN(name) AS sample
        FROM supplier_products
        WHERE supplier_id = %s
        GROUP BY supplier_category_path, supplier_category
        ORDER BY cnt DESC
        """,
        (supplier["id"],),
    )
    if not rows:
        print("    No rows imported yet.")
        return

    for row in rows:
        rule = rules.match(
            {
                "supplier_category": row["supplier_category"],
                "supplier_category_path": row["supplier_category_path"],
                "name": row["sample"],
            }
        )
        label = row["supplier_category_path"] or row["supplier_category"] or "(без категории)"
        applied = f"x{rule.coefficient:g} {rule.rule_name}" if rule else "нет правила"
        print(f"    {row['cnt']:>7,}  {label[:70]:<70}  {applied}")


def recalc_supplier(
    conn: Connection,
    supplier: dict[str, Any],
    rules: PricingRules,
    *,
    dry_run: bool,
    samples: int,
) -> int:
    rows = fetch_all(conn, FETCH_SQL, (supplier["id"],))
    updates: list[tuple[Decimal, int | None, int]] = []
    by_rule: Counter[str] = Counter()
    unmatched = 0
    no_base = 0
    shown = 0

    for row in rows:
        retail, rule = rules.retail_for(row)
        if rule is None:
            unmatched += 1
            continue
        if retail is None:
            no_base += 1
            continue

        by_rule[rule.rule_name] += 1
        if as_decimal(row["price_retail"]) == retail:
            continue

        updates.append((retail, rule.id, row["id"]))
        if shown < samples:
            shown += 1
            print(
                f"    {row['supplier_sku']:<20} {row['price_retail']} -> {retail}"
                f"   ({rule.rule_name})"
            )

    for rule_name, count in by_rule.most_common():
        print(f"    правило '{rule_name}': {count:,} товаров")
    if unmatched:
        print(f"    без правила (цена не тронута): {unmatched:,}")
    if no_base:
        print(f"    нет базовой цены в прайсе: {no_base:,}")

    if not updates:
        print("    Пересчёт не требуется.")
        return 0

    if dry_run:
        print(f"    Изменилось бы цен: {len(updates):,} (dry-run, ничего не записано)")
        return len(updates)

    with conn.cursor() as cur:
        cur.executemany(UPDATE_SQL, updates)
        cur.execute(PROPAGATE_PRODUCTS_SQL, (supplier["id"],))
        products_updated = cur.rowcount
        cur.execute(PROPAGATE_OFFERS_SQL, (supplier["id"],))
        offers_updated = cur.rowcount
    conn.commit()

    print(
        f"    Обновлено: {len(updates):,} строк прайса, "
        f"{products_updated:,} карточек товара, {offers_updated:,} предложений"
    )
    return len(updates)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
        sys.stderr.reconfigure(encoding="utf-8")

    args = parse_args()

    with db_session() as conn:
        suppliers = suppliers_to_process(conn, args.suppliers)
        if not suppliers:
            print("No suppliers with pricing rules. Apply database/add_pricing_rules.sql first.")
            return 1

        total = 0
        for supplier in suppliers:
            rules = load_rules(conn, supplier["id"], use_cache=False)
            print(f"\n{supplier['name']} ({supplier['code']}): {len(rules)} правил")
            if not rules:
                print("    Правил нет - цены оставлены как есть.")
                continue
            print_rules(rules)

            if args.list_categories:
                list_categories(conn, supplier, rules)
                continue

            total += recalc_supplier(
                conn, supplier, rules, dry_run=args.dry_run, samples=args.samples
            )

        if not args.list_categories:
            print(f"\nИтого пересчитано цен: {total:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
