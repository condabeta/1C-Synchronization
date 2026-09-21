#!/usr/bin/env python
"""Push prices and stock from the supplier rows into the catalogue.

An import writes `supplier_products`. Getting those figures into `products` and
`product_supplier_offers` happened only when a row passed through the moderation
queue - and while `is_changed` was never being cleared, the queue stopped moving
and the catalogue drifted: 128,131 products (76%) were showing a price other
than their supplier's current one, 8,713 of them in stock when the supplier had
none left.

This is the set-based catch-up for exactly the fields a supplier is allowed to
own outright:

    python scripts/refresh_catalogue.py --dry-run
    python scripts/refresh_catalogue.py
    python scripts/refresh_catalogue.py --supplier jazzway

Which fields those are is read per supplier from `supplier_field_sync_config`,
the same table the moderation path consults, so a field a manager has protected
(description, name, brand, images) is never touched here. Those still go through
moderation and the discrepancy list.

Only a product's primary supplier updates it, the way its price is decided.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pymysql

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from staging.db import db_session, fetch_all, fetch_one
from staging.sync_config import get_sync_config

# Supplier-owned figures only. Text and images belong to whoever edited them
# last and are settled in moderation, not here.
REFRESHABLE = ("price", "price_retail", "price_old", "stock_qty", "is_available")

# A supplier value of NULL is not news - it means the price list no longer
# carries that article, which is a decision for a person, not something to
# overwrite a working price with. Those rows are counted separately.
CHANGED = """(
    (sp.price IS NOT NULL AND NOT p.price <=> sp.price)
    OR (sp.price_retail IS NOT NULL AND NOT p.price_retail <=> sp.price_retail)
    OR (sp.stock_qty IS NOT NULL AND NOT p.stock_qty <=> sp.stock_qty)
    OR NOT p.is_available <=> sp.is_available
)"""

PRIMARY_JOIN = """
    FROM products p
    JOIN supplier_products sp
      ON sp.product_id = p.id AND sp.supplier_id = %s
     AND (p.primary_supplier_id = sp.supplier_id OR p.primary_supplier_id IS NULL)
"""

DIFF_SQL = f"""
    SELECT COUNT(*) AS cnt {PRIMARY_JOIN} WHERE {CHANGED}
"""

# A price that moves by more than this is not refreshed: it is reported instead.
# Dekomo's exports carry occasional decimal slips - 6,890 to 490 roubles and back
# - and selling at a tenth of the real price is worse than a day-old price.
DEFAULT_MAX_CHANGE = 5.0

SANE = """(
    p.price_retail IS NULL OR p.price_retail = 0 OR sp.price_retail IS NULL
    OR (sp.price_retail / p.price_retail BETWEEN 1 / %s AND %s)
)"""

SUSPECT_SQL = f"""
    SELECT p.internal_sku, p.name, p.price_retail AS old_price, sp.price_retail AS new_price
    {PRIMARY_JOIN}
    WHERE {CHANGED} AND NOT {SANE}
    ORDER BY ABS(LOG(GREATEST(sp.price_retail, 1) / GREATEST(p.price_retail, 1))) DESC
"""

# Priced in the catalogue, no longer priced by the supplier.
DROPPED_SQL = f"""
    SELECT COUNT(*) AS cnt {PRIMARY_JOIN}
    WHERE sp.price IS NULL AND sp.price_retail IS NULL AND p.price_retail IS NOT NULL
"""

SAMPLE_SQL = f"""
    SELECT p.internal_sku, p.price_retail AS old_price, sp.price_retail AS new_price,
           p.stock_qty AS old_stock, sp.stock_qty AS new_stock
    {PRIMARY_JOIN}
    WHERE {CHANGED}
    ORDER BY ABS(COALESCE(sp.price_retail, 0) - COALESCE(p.price_retail, 0)) DESC
    LIMIT %s
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh catalogue prices and stock from supplier rows")
    parser.add_argument("--supplier", action="append", dest="suppliers", metavar="CODE",
                        help="Supplier code (repeatable). Default: all of them.")
    parser.add_argument("--dry-run", action="store_true", help="Count and show examples, write nothing")
    parser.add_argument("--samples", type=int, default=5, help="Example rows to print per supplier")
    parser.add_argument("--max-change", type=float, default=DEFAULT_MAX_CHANGE,
                        help=f"Skip prices that moved by more than this factor (default {DEFAULT_MAX_CHANGE:g})")
    return parser.parse_args()


def suppliers_to_process(conn, codes: list[str] | None) -> list[dict]:
    if codes:
        placeholders = ", ".join(["%s"] * len(codes))
        return fetch_all(
            conn, f"SELECT id, code, name FROM suppliers WHERE code IN ({placeholders}) ORDER BY code", codes
        )
    return fetch_all(conn, "SELECT id, code, name FROM suppliers ORDER BY code")


def refresh(conn, supplier: dict, *, dry_run: bool, samples: int, max_change: float) -> int:
    config = get_sync_config(conn, supplier["id"], use_cache=False)
    fields = [field for field in REFRESHABLE if config.get(field, True)]
    skipped = [field for field in REFRESHABLE if field not in fields]

    if not fields:
        print(f"  {supplier['code']}: все поля защищены, пропуск")
        return 0

    pending = fetch_one(conn, DIFF_SQL, (supplier["id"],))["cnt"]
    dropped = fetch_one(conn, DROPPED_SQL, (supplier["id"],))["cnt"]
    note = f" | не синхронизируются: {', '.join(skipped)}" if skipped else ""
    print(f"  {supplier['code']:<9} расходится: {pending:>7,}{note}")
    if dropped:
        print(f"      поставщик больше не даёт цену: {dropped:,} (цена в каталоге оставлена, нужно решение)")
    if not pending:
        return 0

    for row in fetch_all(conn, SAMPLE_SQL, (supplier["id"], samples)):
        print(
            f"      {str(row['internal_sku'])[:40]:<40} цена {row['old_price']} -> {row['new_price']}"
            f"   остаток {row['old_stock']} -> {row['new_stock']}"
        )
    suspect = fetch_all(conn, SUSPECT_SQL, (supplier["id"], max_change, max_change))
    if suspect:
        print(f"      цена изменилась больше чем в {max_change:g} раз - не применяем, {len(suspect)} шт.:")
        for row in suspect[:samples]:
            print(
                f"        {str(row['internal_sku'])[:38]:<38} {row['old_price']} -> {row['new_price']}"
                f"   {str(row['name'])[:34]}"
            )

    if dry_run:
        return pending

    # COALESCE, so a missing supplier figure leaves the catalogue's alone.
    # is_available is a real 0/1 either way, so it is taken as it comes.
    def assign(alias: str, field: str) -> str:
        if field == "is_available":
            return f"{alias}.{field} = sp.{field}"
        return f"{alias}.{field} = COALESCE(sp.{field}, {alias}.{field})"

    assignments = ", ".join(assign("p", field) for field in fields)
    # product_supplier_offers carries a subset of the columns: no price_old.
    offer_columns = {"price", "price_retail", "stock_qty", "is_available"}
    offer_fields = [field for field in fields if field in offer_columns and field != "is_available"]
    offer_assignments = ", ".join(assign("o", field) for field in offer_fields)

    with conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE products p
            JOIN supplier_products sp
              ON sp.product_id = p.id AND sp.supplier_id = %s
             AND (p.primary_supplier_id = sp.supplier_id OR p.primary_supplier_id IS NULL)
            SET {assignments}, p.updated_at = NOW()
            WHERE {SANE}
            """,
            (supplier["id"], max_change, max_change),
        )
        products = cur.rowcount
        cur.execute(
            f"""
            UPDATE product_supplier_offers o
            JOIN supplier_products sp
              ON sp.product_id = o.product_id AND sp.supplier_id = o.supplier_id
            JOIN products p ON p.id = o.product_id
            SET {offer_assignments}
            WHERE o.supplier_id = %s AND {SANE}
            """,
            (supplier["id"], max_change, max_change),
        )
        offers = cur.rowcount
    conn.commit()
    print(f"      обновлено: {products:,} карточек, {offers:,} предложений")
    return products


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
        sys.stderr.reconfigure(encoding="utf-8")

    args = parse_args()
    try:
        with db_session() as conn:
            suppliers = suppliers_to_process(conn, args.suppliers)
            total = 0
            for supplier in suppliers:
                total += refresh(conn, supplier, dry_run=args.dry_run, samples=args.samples,
                                 max_change=args.max_change)
            print(f"\nИтого: {total:,}" + (" (dry-run, ничего не записано)" if args.dry_run else ""))
        return 0
    except pymysql.err.OperationalError as exc:
        print("Не удалось подключиться к MySQL.", file=sys.stderr)
        print(f"  {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
