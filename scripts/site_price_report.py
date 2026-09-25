#!/usr/bin/env python
"""What would change on svetoyar.pro if the price lists were applied.

The shop is live - 6,700 orders - and the client asked to see the changes
before they happen. So nothing here writes anywhere: it reads a snapshot of the
site's catalogue, compares it with ours, and writes an Excel file for a person
to go through.

    python scripts/site_price_report.py
    python scripts/site_price_report.py --max-change 3 --out D:\\changes.xlsx

The snapshot is three CSV files exported from the site's phpMyAdmin:

    SELECT product_id, sku, status, quantity, price, image, manufacturer_id,
           date_added, date_modified FROM oc_product;      -> oc_product.csv
    SELECT product_id, name FROM oc_product_description;   -> oc_product_description.csv
    SELECT manufacturer_id, name FROM oc_manufacturer;     -> oc_manufacturer.csv

Two things make the comparison honest rather than arithmetic:

**Matching is verified by name.** The site keys products on the supplier's
article, and ours agree except for Dekomo, whose codes we carry with a prefix
the site drops. Stripping that prefix leaves short codes that collide: our
"Подвесной светильник Mantra Cono 9458" met the site's "Заглушка Nowodvorski
Profile 9458". So a pair is kept only when the two names share a word that is
not a number, or the brands agree.

**Dekomo prices by the pack.** «Кратность товара» says how many pieces are in
one, and the site sells pieces. A Feron cord is 13,350 roubles from Dekomo and
267 on the site - fifty to a pack, and both are right. Prices are divided by
the multiplicity before anything is compared.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from staging.db import db_session, fetch_all

csv.field_size_limit(10 ** 7)

SNAPSHOT_DIR = Path(r"D:\projects\1C")
DEFAULT_OUT = SNAPSHOT_DIR / "Изменения цен и остатков на сайте.xlsx"

# A price that moves by more than this is reported separately rather than mixed
# in with the routine changes - it is usually a data error on one side.
DEFAULT_MAX_CHANGE = 5.0

SUPPLIERS = {
    "dekomo": "Декомо", "arlight": "Арлайт", "swg": "SWG", "jazzway": "Jazzway",
    "salux": "Салюкс", "crystal": "LED Crystal", "viasvet": "ВиаСвет",
}

WORD_RE = re.compile(r"[a-zа-яё]{3,}", re.I)  # letters only: a shared number proves nothing

# Words every second lighting product contains. Two products sharing only these
# share nothing: "Потолочный светильник Lightstar Binoco" and "Светильник
# SP-LAGERN-MOTION-L885-150W" are not the same product, and they were paired
# because both say "светильник".
GENERIC = {
    "светильник", "светильники", "светильника", "лампа", "лампы", "лампочка",
    "потолочный", "потолочная", "потолочные", "подвесной", "подвесная", "подвесные",
    "настенный", "настенная", "встраиваемый", "встраиваемая", "накладной", "накладная",
    "настольная", "настольный", "светодиодный", "светодиодная", "светодиодные",
    "декоративный", "декоративная", "уличный", "уличная", "лента", "модуль",
    "профиль", "блок", "питания", "для", "под", "теплый", "белый", "черный",
}

# The same brand, written the way each side happens to write it.
BRAND_ALIASES = {
    "arlight": "arlight", "арлайт": "arlight", "ардеколед": "ardecoled",
    "maytoni": "maytoni", "майтони": "maytoni", "maytoniledstrip": "maytoni",
    "jazzway": "jazzway", "джазвей": "jazzway", "faza": "jazzway", "фаza": "jazzway",
    "ledcrystal": "crystal", "салюкс": "salux", "salux": "salux",
}
HEADER_FILL = PatternFill("solid", fgColor="DDEBF7")
WARN_FILL = PatternFill("solid", fgColor="FCE4D6")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Report the price and stock changes the site would receive")
    parser.add_argument("--snapshot", type=Path, default=SNAPSHOT_DIR, help="Folder with the three CSV exports")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Excel file to write")
    parser.add_argument("--max-change", type=float, default=DEFAULT_MAX_CHANGE,
                        help=f"Above this factor a change is listed for checking (default {DEFAULT_MAX_CHANGE:g})")
    return parser.parse_args()


def norm(value: str | None) -> str:
    return (value or "").strip().lstrip(".").upper().replace(" ", "")


def words(text: str | None) -> set[str]:
    return {w.lower() for w in WORD_RE.findall(text or "")} - GENERIC


def brand_key(value: str | None) -> str:
    key = re.sub(r"[^a-zа-яё0-9]", "", (value or "").lower())
    return BRAND_ALIASES.get(key, key)


def load_site(folder: Path) -> dict[str, dict]:
    names, brands = {}, {}
    with (folder / "oc_product_description.csv").open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            names.setdefault(row["product_id"], row["name"])
    with (folder / "oc_manufacturer.csv").open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            brands[row["manufacturer_id"]] = row["name"]

    # An article is not unique on the site: 7252 is both a Voltega lamp at 214
    # roubles and a Mantra table light at 32,108, and 052016 is both a Lightstar
    # and an Arlight fitting. Keeping only the first meant our Arlight row was
    # compared with somebody else's product - which is what the client caught.
    site: dict[str, list[dict]] = {}
    with (folder / "oc_product.csv").open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            key = norm(row["sku"])
            if not key:
                continue
            row["name"] = names.get(row["product_id"], "")
            row["brand"] = brands.get(row["manufacturer_id"], "")
            site.setdefault(key, []).append(row)
    return site


def pick(ours: dict, candidates: list[dict]) -> dict | None:
    """Which of the site's products with this article is ours, if any.

    The brand decides. Where both sides name one and they disagree, it is a
    different manufacturer's product that happens to share an article number -
    reject it, however similar the names look. Only where a brand is missing
    does the name get a say, and then it has to share a word that is not one
    every luminaire has.
    """
    ours_brand = brand_key(ours.get("brand"))
    if ours_brand:
        same_brand = [c for c in candidates if brand_key(c["brand"]) == ours_brand]
        if same_brand:
            return same_brand[0]
        if any(brand_key(c["brand"]) for c in candidates):
            return None  # every candidate names a different manufacturer
    for candidate in candidates:
        if not brand_key(candidate["brand"]) and words(ours["name"]) & words(candidate["name"]):
            return candidate
    return None


def match(ours: list[dict], site: dict[str, list[dict]]) -> list[tuple[dict, dict]]:
    """Our rows paired with site products, one supplier row per site product.

    Where a product reaches us from two suppliers - Arlight sells directly and
    Dekomo resells the same fittings - the supplier whose own brand it is wins.
    The client buys those direct, and the direct price is the right one.
    """
    claims: dict[str, tuple[dict, dict]] = {}
    for row in ours:
        for field in ("supplier_sku", "manufacturer_code"):
            key = norm(row[field])
            keys = [key] + ([key.split("_", 1)[1]] if "_" in key else [])
            found = next((pick(row, site[k]) for k in keys if k in site), None)
            if not found:
                continue
            product_id = found["product_id"]
            current = claims.get(product_id)
            if current is None or beats(row, current[0], found):
                claims[product_id] = (row, found)
            break
    return list(claims.values())


DIRECT_BRANDS = {"arlight": "arlight", "jazzway": "jazzway", "salux": "salux"}


def beats(contender: dict, holder: dict, product: dict) -> bool:
    """Should this supplier row replace the one already claiming the product?"""
    brand = brand_key(product["brand"])
    contender_direct = DIRECT_BRANDS.get(brand) == contender["code"]
    holder_direct = DIRECT_BRANDS.get(brand) == holder["code"]
    if contender_direct != holder_direct:
        return contender_direct
    return False


def decimal_or_none(value) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        return None


def build_rows(pairs: list[tuple[dict, dict]], max_change: float) -> tuple[list[dict], list[dict]]:
    """(routine changes, changes that need a look)."""
    routine, suspect = [], []
    for ours, theirs in pairs:
        if theirs["status"] != "1":
            continue  # hidden on the site: not for sale, not our business to reprice
        pack = decimal_or_none(ours.get("pack")) or Decimal(1)
        if pack <= 0:
            pack = Decimal(1)
        new_price = decimal_or_none(ours["price_retail"])
        old_price = decimal_or_none(theirs["price"])
        if new_price is not None:
            new_price = (new_price / pack).quantize(Decimal("0.01"))

        new_stock = decimal_or_none(ours["stock_qty"])
        old_stock = decimal_or_none(theirs["quantity"])

        price_moves = new_price is not None and old_price is not None and new_price != old_price
        stock_moves = new_stock is not None and old_stock is not None and new_stock != old_stock
        if not price_moves and not stock_moves:
            continue

        ratio = float(new_price / old_price) if price_moves and old_price and old_price > 0 else 1.0
        record = {
            "Артикул на сайте": theirs["sku"],
            "Наименование": theirs["name"],
            "Бренд": theirs["brand"],
            "Поставщик": SUPPLIERS.get(ours["code"], ours["code"]),
            "Цена сейчас, ₽": float(old_price) if old_price is not None else None,
            "Цена по прайсу, ₽": float(new_price) if new_price is not None else None,
            "Разница, %": round((ratio - 1) * 100, 1) if price_moves else None,
            "Остаток сейчас": float(old_stock) if old_stock is not None else None,
            "Остаток по прайсу": float(new_stock) if new_stock is not None else None,
            "Штук в упаковке": int(pack) if pack != 1 else None,
            "Артикул поставщика": ours["supplier_sku"],
            "ID на сайте": int(theirs["product_id"]),
        }
        if price_moves and (ratio > max_change or ratio < 1 / max_change):
            record["Что проверить"] = "Цена меняется в разы — возможна ошибка в прайсе или разная упаковка"
            suspect.append(record)
        else:
            routine.append(record)

    routine.sort(key=lambda r: (r["Поставщик"], -(r["Разница, %"] or 0)))
    suspect.sort(key=lambda r: -abs(r["Разница, %"] or 0))
    return routine, suspect


def write_sheet(workbook, title: str, rows: list[dict], note: str | None = None) -> None:
    sheet = workbook.create_sheet(title)
    if not rows:
        sheet["A1"] = "Изменений нет"
        return
    headers = list(rows[0].keys())
    if note:
        sheet.append([note])
        sheet["A1"].font = Font(italic=True)
        sheet.append([])
    sheet.append(headers)
    for cell in sheet[sheet.max_row]:
        cell.font = Font(bold=True)
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    for row in rows:
        sheet.append([row.get(h) for h in headers])
    widths = {"Наименование": 52, "Артикул на сайте": 22, "Артикул поставщика": 24,
              "Что проверить": 46, "Бренд": 16, "Поставщик": 12}
    for index, header in enumerate(headers, start=1):
        letter = openpyxl.utils.get_column_letter(index)
        sheet.column_dimensions[letter].width = widths.get(header, 16)
    sheet.freeze_panes = sheet.cell(row=sheet.max_row - len(rows) + 1, column=1).coordinate
    sheet.auto_filter.ref = sheet.dimensions


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
        sys.stderr.reconfigure(encoding="utf-8")

    args = parse_args()
    missing = [name for name in ("oc_product.csv", "oc_product_description.csv", "oc_manufacturer.csv")
               if not (args.snapshot / name).exists()]
    if missing:
        print(f"Нет выгрузок сайта в {args.snapshot}: {', '.join(missing)}", file=sys.stderr)
        return 1

    site = load_site(args.snapshot)
    print(f"Каталог сайта: {len(site):,} артикулов")

    with db_session() as conn:
        ours = fetch_all(
            conn,
            """
            SELECT s.code, sp.supplier_sku, sp.manufacturer_code, sp.name, sp.brand,
                   sp.price_retail, sp.stock_qty,
                   sp.attributes_json->>'$."Кратность товара"' AS pack
            FROM supplier_products sp
            JOIN suppliers s ON s.id = sp.supplier_id
            """,
        )
    print(f"Наш каталог: {len(ours):,} позиций")

    pairs = match(ours, site)
    print(f"Сопоставлено: {len(pairs):,}")

    routine, suspect = build_rows(pairs, args.max_change)
    print(f"Изменений: {len(routine):,} | на проверку: {len(suspect):,}")

    workbook = openpyxl.Workbook()
    summary = workbook.active
    summary.title = "Сводка"
    by_supplier = Counter(row["Поставщик"] for row in routine)
    packed = sum(1 for row in routine if row["Штук в упаковке"])
    price_only = sum(1 for r in routine if r["Разница, %"] is not None)
    stock_only = sum(1 for r in routine if r["Разница, %"] is None)
    for line in (
        ["Что изменится на сайте, если применить прайсы поставщиков"],
        [],
        ["Товаров на сайте (в продаже, сопоставлено с прайсом)", len(pairs)],
        ["Изменений всего", len(routine)],
        ["  из них меняется цена", price_only],
        ["  только остаток", stock_only],
        ["Отложено на проверку (цена меняется в разы)", len(suspect)],
        [],
        ["По поставщикам:"],
        *[[f"  {name}", count] for name, count in by_supplier.most_common()],
        [],
        ["Про упаковки:"],
        ["  Декомо указывает «Кратность товара» — такие цены пересчитаны на штуку."],
        [f"  пересчитано позиций", packed],
        ["  У остальных поставщиков колонки с упаковкой нет. Если на сайте товар"],
        ["  продаётся поштучно, а в прайсе идёт блистером или катушкой, разница"],
        ["  попадёт в лист «Проверить» — там такие случаи и собраны."],
    ):
        summary.append(line)
    summary["A1"].font = Font(bold=True, size=13)
    summary.column_dimensions["A"].width = 52
    summary.column_dimensions["B"].width = 14

    write_sheet(workbook, "Изменения", routine)
    write_sheet(
        workbook, "Проверить", suspect,
        note="Цена меняется более чем в %g раз. Обычно это ошибка в прайсе или разная упаковка — "
             "мы такие не применяем без вашего решения." % args.max_change,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(args.out)
    print(f"Файл: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
