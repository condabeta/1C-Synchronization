#!/usr/bin/env python
"""Hide the goods a brand no longer supplies.

    python scripts/hide_discontinued.py --brand 6063 --brand Россия
    python scripts/hide_discontinued.py --brand 6063 --apply

Of the 7,131 site products with no supplier price behind them, Анна released
three brands on 30.09.2026: «6063» (293), «Россия» (253) and «Светояр» (406).
Arlight and Jazzway she is still reading, and the remaining 123 brands have had
no decision, so nothing here selects a brand on its own.

«6063» is an aluminium alloy, not a manufacturer - a brand column filled in from
the profile the goods are extruded from. It is a real row on the site all the
same, which is why it is named like any other.

The product is switched off, not deleted: the page, its photographs and its
order history survive, and putting it back is one flag. Nothing touches the live
site - the change is queued in `sync_outbox` for the exchange to carry out.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import openpyxl

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from staging.db import db_session

SOURCE = Path(r"D:\projects\1C\Товары без прайса - по брендам.xlsx")
SHEET = "Все позиции"
REASON = "нет у поставщика, снят с производства (Анна, 30.09.2026)"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Queue discontinued goods to be hidden")
    parser.add_argument("--brand", action="append", dest="brands", required=True,
                        help="site brand to hide; repeat for several")
    parser.add_argument("--file", type=Path, default=SOURCE)
    parser.add_argument("--apply", action="store_true", help="write to sync_outbox (default: report only)")
    return parser.parse_args()


def read_rows(path: Path, brands: set[str]) -> tuple[list[dict], Counter]:
    workbook = openpyxl.load_workbook(path, data_only=True, read_only=True)
    sheet = workbook[SHEET]
    rows = sheet.iter_rows(values_only=True)
    header = next(rows)
    index = {str(name): position for position, name in enumerate(header)}
    brand_at = index["Бренд"]
    sku_at = index["Артикул на сайте"]
    name_at = index["Наименование"]
    id_at = index["ID на сайте"]

    wanted, seen = [], Counter()
    for row in rows:
        brand = str(row[brand_at]) if row[brand_at] else ""
        if not brand:
            continue
        seen[brand] += 1
        if brand in brands:
            wanted.append({
                "brand": brand,
                "sku": str(row[sku_at] or "").strip(),
                "name": str(row[name_at] or ""),
                "product_id": row[id_at],
            })
    workbook.close()
    return wanted, seen


def enqueue(conn, products: list[dict]) -> int:
    """Queue each product once, leaving any already-pending row alone."""
    written = 0
    with conn.cursor() as cur:
        for product in products:
            payload = json.dumps(
                {
                    "opencart_product_id": int(product["product_id"]),
                    "sku": product["sku"],
                    "manufacturer": product["brand"],
                    "status": 0,
                    "reason": REASON,
                },
                ensure_ascii=False,
            )
            cur.execute(
                """
                INSERT INTO sync_outbox (entity_type, entity_id, target_system, action, payload_json)
                SELECT 'product', %s, 'opencart', 'update', CAST(%s AS JSON)
                FROM DUAL WHERE NOT EXISTS (
                    SELECT 1 FROM (SELECT id FROM sync_outbox
                        WHERE entity_type = 'product' AND entity_id = %s
                          AND target_system = 'opencart' AND status = 'pending') AS pending
                )
                """,
                (product["product_id"], payload, product["product_id"]),
            )
            written += cur.rowcount
    conn.commit()
    return written


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
        sys.stderr.reconfigure(encoding="utf-8")

    args = parse_args()
    if not args.file.exists():
        print(f"Файл не найден: {args.file}", file=sys.stderr)
        return 1

    brands = set(args.brands)
    products, seen = read_rows(args.file, brands)

    for brand in sorted(brands):
        if brand not in seen:
            print(f"Бренда «{brand}» в списке нет — проверьте написание", file=sys.stderr)
            return 1
        print(f"  {brand:<12} {seen[brand]:>5}")
    print(f"  {'ИТОГО':<12} {len(products):>5}")

    missing = [p for p in products if not p["product_id"]]
    if missing:
        print(f"\nБез ID на сайте, пропускаем: {len(missing)}")
        products = [p for p in products if p["product_id"]]

    if not args.apply:
        print("\nЭто предпросмотр. Чтобы поставить в очередь: --apply")
        return 0

    with db_session() as conn:
        written = enqueue(conn, products)
    print(f"\nВ очередь на скрытие поставлено: {written} из {len(products)}")
    if written < len(products):
        print("  остальные уже стояли в очереди")
    print("На сайте пока ничего не изменилось — скроет обмен, когда он будет развёрнут.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
