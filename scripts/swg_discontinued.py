#!/usr/bin/env python
"""Withdraw discontinued SWG goods from the site.

    python scripts/swg_discontinued.py                     # show what would happen
    python scripts/swg_discontinued.py --apply             # queue the withdrawal
    python scripts/swg_discontinued.py 002325 002326 --apply

Анна wrote on 30.09.2026 that 002325, 002326, 002016, 002033 and 002300 are out
of production at SWG, and to take them down.

Every one of those five numbers is carried by two products on the site: one
SWG, one «Светояр». So the lookup is by manufacturer and article together, never
by article alone - that mistake is the whole reason
`scripts/own_article_report.py` exists. Only the SWG product of each pair is
withdrawn.

Nothing is written to the live site. The withdrawal is queued in `sync_outbox`
for the exchange to carry out, which is reversible: the product is switched off,
not deleted, so its page, photographs and history survive.

Note that four of the five are still in SWG's price list of 25.09.2026 with
prices. SWG telling Анна they are discontinued beats a price list that has not
been tidied up, so they are withdrawn - but the report says so out loud.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from staging.db import db_session, fetch_all

SITE_DIR = Path(r"D:\projects\1C")
PRODUCTS_CSV = SITE_DIR / "oc_product.csv"
DESCRIPTIONS_CSV = SITE_DIR / "oc_product_description.csv"
MANUFACTURERS_CSV = SITE_DIR / "oc_manufacturer.csv"

SITE_MANUFACTURER = "SWG"
DISCONTINUED = ["002325", "002326", "002016", "002033", "002300"]
REASON = "снят с производства (SWG, 30.09.2026)"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Queue discontinued SWG goods for withdrawal")
    parser.add_argument("articles", nargs="*", default=None, help="site articles (default: the five of 30.09)")
    parser.add_argument("--apply", action="store_true", help="write to sync_outbox (default: report only)")
    return parser.parse_args()


def read_site(articles: set[str]) -> tuple[list[dict], list[dict]]:
    """The SWG products carrying these articles, and the namesakes left alone."""
    csv.field_size_limit(10_000_000)
    manufacturers, names = {}, {}
    with MANUFACTURERS_CSV.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            manufacturers[row["manufacturer_id"]] = row.get("name") or ""
    with DESCRIPTIONS_CSV.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            names.setdefault(row["product_id"], row.get("name") or "")

    targets, namesakes = [], []
    with PRODUCTS_CSV.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            sku = (row.get("sku") or "").strip()
            if sku not in articles:
                continue
            product = {
                "product_id": row.get("product_id"),
                "sku": sku,
                "brand": manufacturers.get(row.get("manufacturer_id") or "", ""),
                "name": names.get(row.get("product_id") or "", ""),
                "price": row.get("price"),
                "status": "в продаже" if (row.get("status") or "") == "1" else "выключен",
            }
            (targets if product["brand"] == SITE_MANUFACTURER else namesakes).append(product)
    return targets, namesakes


def still_priced(conn, articles: set[str]) -> set[str]:
    """Which of these SWG still lists, by the prefix their 1C writes."""
    codes = {"00-" + article.zfill(8): article for article in articles if article.isdigit()}
    if not codes:
        return set()
    placeholders = ", ".join(["%s"] * len(codes))
    rows = fetch_all(
        conn,
        f"""
        SELECT sp.supplier_sku FROM supplier_products sp
        JOIN suppliers s ON s.id = sp.supplier_id
        WHERE s.code = 'swg' AND sp.supplier_sku IN ({placeholders})
        """,
        tuple(codes),
    )
    return {codes[row["supplier_sku"]] for row in rows}


def enqueue(conn, targets: list[dict]) -> int:
    """Queue each withdrawal once, leaving any already-pending row alone."""
    written = 0
    with conn.cursor() as cur:
        for product in targets:
            payload = json.dumps(
                {
                    "opencart_product_id": int(product["product_id"]),
                    "sku": product["sku"],
                    "manufacturer": SITE_MANUFACTURER,
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
    articles = set(args.articles or DISCONTINUED)
    for path in (PRODUCTS_CSV, DESCRIPTIONS_CSV, MANUFACTURERS_CSV):
        if not path.exists():
            print(f"Выгрузка сайта не найдена: {path}", file=sys.stderr)
            return 1

    targets, namesakes = read_site(articles)
    with db_session() as conn:
        priced = still_priced(conn, articles)

        print(f"Снять с продажи ({SITE_MANUFACTURER}):")
        for product in sorted(targets, key=lambda p: p["sku"]):
            note = "  ⚠ ещё есть в прайсе SWG" if product["sku"] in priced else ""
            print(f"  {product['sku']}  {str(product['name'])[:44]:<46} "
                  f"{product['status']:<10} id={product['product_id']}{note}")

        missing = articles - {p["sku"] for p in targets}
        for article in sorted(missing):
            print(f"  {article}  — товара SWG с таким артикулом на сайте нет")

        print(f"\nНе трогаем — тот же артикул у другого производителя:")
        for product in sorted(namesakes, key=lambda p: p["sku"]):
            print(f"  {product['sku']}  [{product['brand']}]  {str(product['name'])[:40]:<42} "
                  f"id={product['product_id']}")

        if not args.apply:
            print(f"\nЭто предпросмотр. Чтобы поставить в очередь: --apply")
            return 0

        written = enqueue(conn, targets)

    print(f"\nВ очередь на снятие поставлено: {written} из {len(targets)}")
    if written < len(targets):
        print("  остальные уже стояли в очереди")
    print("На сайте пока ничего не изменилось — снимет обмен, когда он будет развёрнут.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
