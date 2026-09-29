#!/usr/bin/env python
"""Which site products carry one of our hand-made articles, and whose it is.

    python scripts/own_article_report.py

Анна keeps three price lists by hand - Salux goods sold under our own number,
SWG, and Точка Зрения - and all three number their articles 000001 upwards. On
the site 127 such articles are carried by 276 products. The article alone cannot
say which is which; the manufacturer column can, and always could.

The report that went to the client on 28.09.2026 did not use it. It proposed
replacing the article on 758 site products with a Salux marking, and 288 of those
products belong to seven other brands - SWG, Точка Зрения, Artpole, DesignLed,
Arlight, EasyDim, Lightstar. Applying it would have renamed them. This report
replaces it and is manufacturer-aware throughout.

Three sheets:

* **Замена артикулов** - only the Salux goods, sold on the site under «Россия»
  or «Светояр». These are the ones whose article may be replaced with the Salux
  marking, because the conformity documents are issued against the marking
  (Арсений, 23.09.2026).
* **Не трогать** - the other products sharing those article numbers, with the
  brand each belongs to. This sheet exists to be read before anything is edited.
* **Точка Зрения** - their 108 articles as Анна sent them on 29.09.2026, so the
  numbers can be checked against the site.

Reads the site CSV exports rather than the site itself, which is unreachable.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from staging.db import db_session, fetch_all

SITE_DIR = Path(r"D:\projects\1C")
PRODUCTS_CSV = SITE_DIR / "oc_product.csv"
DESCRIPTIONS_CSV = SITE_DIR / "oc_product_description.csv"
MANUFACTURERS_CSV = SITE_DIR / "oc_manufacturer.csv"
OUTPUT = SITE_DIR / "Артикулы 000001 - кому какие принадлежат.xlsx"

# Whose goods the site sells under our own numbers. «Светояр» and «Россия» are
# both the Salux ones: the price list writes one in the manufacturer column and
# the other on the site.
SALUX_MANUFACTURERS = {"Россия", "Светояр"}

HEADER_FILL = PatternFill("solid", fgColor="D9E1F2")
WARN_FILL = PatternFill("solid", fgColor="FCE4D6")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Report on hand-made site articles")
    parser.add_argument("--out", type=Path, default=OUTPUT)
    return parser.parse_args()


def read_site(wanted: set[str]) -> list[dict]:
    """Site products carrying one of our hand-made articles, with brand and name.

    `wanted` is the set of articles our own price lists actually use. Six digits
    alone means nothing - 27,146 site products have a six-digit article and
    almost all of them are ordinary manufacturer codes, 15,911 of them Arlight's.
    Only the numbers we ourselves issued are in contention.
    """
    csv.field_size_limit(10_000_000)

    manufacturers: dict[str, str] = {}
    with MANUFACTURERS_CSV.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            manufacturers[row["manufacturer_id"]] = row.get("name") or ""

    names: dict[str, str] = {}
    with DESCRIPTIONS_CSV.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            names.setdefault(row["product_id"], row.get("name") or "")

    products = []
    with PRODUCTS_CSV.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            sku = (row.get("sku") or "").strip()
            if sku not in wanted:
                continue
            products.append({
                "product_id": row.get("product_id"),
                "sku": sku,
                "brand": manufacturers.get(row.get("manufacturer_id") or "", ""),
                "name": names.get(row.get("product_id") or "", ""),
                "price": row.get("price"),
                "quantity": row.get("quantity"),
                "status": "в продаже" if (row.get("status") or "") == "1" else "выключен",
            })
    return products


def read_own_articles(conn) -> dict[str, list[dict]]:
    """Our articles, grouped by the site manufacturer they are sold under."""
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in fetch_all(
        conn,
        """
        SELECT s.code AS supplier, oa.own_sku, oa.own_name, oa.supplier_marking,
               oa.supplier_name, oa.site_manufacturer, oa.dealer_price, oa.category
        FROM own_articles oa LEFT JOIN suppliers s ON s.id = oa.supplier_id
        ORDER BY oa.own_sku
        """,
    ):
        grouped[row["own_sku"]].append(row)
    return grouped


def write_sheet(sheet, headers: list[str], widths: list[int], rows: list[list]) -> None:
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    for row in rows:
        sheet.append(row)
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[sheet.cell(row=1, column=index).column_letter].width = width
    sheet.freeze_panes = "A2"


def build(out_path: Path) -> int:
    with db_session() as conn:
        own = read_own_articles(conn)
    site = read_site(set(own))

    replace_rows, leave_rows = [], []
    for product in sorted(site, key=lambda p: (p["sku"], p["brand"])):
        ours = [row for row in own.get(product["sku"], []) if row["supplier"] == "salux"]
        if product["brand"] in SALUX_MANUFACTURERS and ours:
            marking = ours[0]["supplier_marking"]
            replace_rows.append([
                product["sku"], product["brand"], product["name"], product["price"],
                product["status"], marking, ours[0]["own_name"], product["product_id"],
                "" if marking else "в нашем прайсе нет маркировки — не заменять",
            ])
        else:
            theirs = own.get(product["sku"], [])
            whose = ", ".join(sorted({row["site_manufacturer"] or row["supplier"] or "?"
                                      for row in theirs})) or "нет в наших прайсах"
            leave_rows.append([
                product["sku"], product["brand"], product["name"], product["price"],
                product["status"], whose, product["product_id"],
            ])

    tochka = [
        [row["own_sku"], row["own_name"], row["supplier_name"], row["dealer_price"], row["category"]]
        for row in sorted(
            (r for rows in own.values() for r in rows if r["supplier"] == "tochka_zreniya"),
            key=lambda r: r["own_sku"],
        )
    ]

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Замена артикулов"
    write_sheet(
        sheet,
        ["Артикул на сайте", "Производитель на сайте", "Наименование на сайте",
         "Цена, ₽", "Статус", "Маркировка Салюкс (заменить на неё)", "Наше наименование",
         "ID на сайте", "Внимание"],
        [16, 20, 52, 11, 11, 32, 34, 11, 40],
        replace_rows,
    )
    for row in sheet.iter_rows(min_row=2):
        if row[8].value:
            for cell in row:
                cell.fill = WARN_FILL

    write_sheet(
        workbook.create_sheet("Не трогать"),
        ["Артикул на сайте", "Производитель на сайте", "Наименование на сайте",
         "Цена, ₽", "Статус", "Чей это артикул", "ID на сайте"],
        [16, 20, 52, 11, 11, 22, 11],
        leave_rows,
    )
    write_sheet(
        workbook.create_sheet("Точка Зрения"),
        ["Артикул", "Наше наименование", "Наименование поставщика", "Цена, ₽", "Категория"],
        [16, 44, 44, 11, 40],
        tochka,
    )
    workbook.save(out_path)

    brands = defaultdict(int)
    for row in leave_rows:
        brands[row[1]] += 1
    print(f"Наших артикулов в прайсах: {len(own):,}")
    print(f"Товаров на сайте с таким артикулом: {len(site):,}")
    print(f"  под Салюксом («Россия», «Светояр») — можно заменять: {len(replace_rows):,}")
    print(f"  чужие, трогать нельзя: {len(leave_rows):,}")
    for brand, count in sorted(brands.items(), key=lambda item: -item[1]):
        print(f"      {brand or '(без производителя)':<18} {count:>4}")
    print(f"\nФайл: {out_path}")
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
        sys.stderr.reconfigure(encoding="utf-8")
    args = parse_args()
    for path in (PRODUCTS_CSV, DESCRIPTIONS_CSV, MANUFACTURERS_CSV):
        if not path.exists():
            print(f"Выгрузка сайта не найдена: {path}", file=sys.stderr)
            return 1
    return build(args.out)


if __name__ == "__main__":
    raise SystemExit(main())
