"""Точка Зрения - fibre-optic lighting and the Premier projectors.

Анна sent this on 29.09.2026 to correct something we had wrong. Article 000001
on the site is «Светодиодный проектор Premier MINI», and we had been treating it
as Salux. It is not: Точка Зрения is a separate supplier, and their price list is
kept by hand with articles running 000001 upwards - the same numbers Salux uses,
and the same numbers SWG uses. Everything named «проектор Premier» or «Общий ввод
для светодиодных проекторов Тип 7» is theirs.

The site has always known the difference and we had not: it stores a
manufacturer against each product, and 127 six-digit articles there are carried
by 276 products under «Россия», «SWG» and «Точка Зрения». So nothing in this
importer may key on the article alone.

The file is small - 108 rows over fifteen categories - and carries two names per
product: theirs, which the client copies into the 1C comment, and ours, which is
what the shop shows. Both are kept.

Stock is a flag rather than a count: every row in stock reads 1.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterator

import openpyxl

from staging.importers.common import clean, content_hash, parse_decimal

SUPPLIER_CODE = "tochka_zreniya"
SOURCE_CODE = "price_xlsx"
PRICE_FILE = Path(r"D:\projects\1C\Точка Зрения.xlsx")

# How the site tells these apart from Salux's and SWG's identical articles.
SITE_MANUFACTURER = "Точка Зрения"
BRAND = "Точка Зрения"

SHEET = "Лист1"
FIRST_DATA_ROW = 3  # row 1 groups the columns, row 2 names them

ARTICLE = 0
CATEGORY = 1
MANUFACTURER = 2
SUPPLIER_NAME = 3  # «Наименование точки зрения» - goes into the 1C comment
OWN_NAME = 4  # «Наше наименование» - what the shop shows
PRICE = 5
IN_STOCK = 6
URL = 7


def _cell(row: tuple[Any, ...], index: int) -> Any:
    return row[index] if index < len(row) else None


def iter_tochka_zreniya_rows(
    path: Path = PRICE_FILE, *, progress: Callable[[str], None] | None = None
) -> Iterator[dict[str, Any]]:
    """Every priced article in the file.

    Articles are not all numeric - the projectors run 000001 upwards while the
    fibre kits are named outright ("Premier F108", "P-LINE 60"). Both are the
    article as the client uses it, so neither is normalised.
    """
    workbook = openpyxl.load_workbook(path, data_only=True)
    sheet = workbook[SHEET]
    count = 0

    for row in sheet.iter_rows(min_row=FIRST_DATA_ROW, values_only=True):
        article = clean(_cell(row, ARTICLE))
        if not article:
            continue  # blank separator rows between the categories
        price = parse_decimal(_cell(row, PRICE))
        if price is None or price <= 0:
            continue

        own_name = clean(_cell(row, OWN_NAME))
        supplier_name = clean(_cell(row, SUPPLIER_NAME))
        category = clean(_cell(row, CATEGORY))
        url = clean(_cell(row, URL))
        # The column holds a flag, not a quantity: every stocked row reads 1.
        in_stock = 1 if parse_decimal(_cell(row, IN_STOCK)) else 0

        attributes = {
            "Наименование поставщика": supplier_name,
            "Производитель": clean(_cell(row, MANUFACTURER)),
            "Производитель на сайте": SITE_MANUFACTURER,
        }
        attributes = {key: value for key, value in attributes.items() if value}

        item = {
            "supplier_sku": article[:128],
            "supplier_sku_raw": article,
            "name": own_name or supplier_name or article,
            "brand": BRAND,
            "manufacturer_code": article[:255],
            "description": None,
            "supplier_category": category,
            "supplier_category_path": category,
            "price": price,
            "price_retail": price,
            "price_old": None,
            "stock_qty": None,  # a flag, not a count
            "is_available": in_stock,
            "product_url": url[:1024] if url else None,
            "barcode": None,
            "images_json": [],
            "attributes_json": attributes,
            "raw_data_json": None,
        }
        item["content_hash"] = content_hash(
            {
                "supplier_sku": item["supplier_sku"],
                "name": item["name"],
                "price": str(price),
                "is_available": in_stock,
                "attributes_json": attributes,
            }
        )
        count += 1
        yield item

    workbook.close()
    if progress:
        progress(f"  {path.name}: {count:,} товаров")


def iter_own_article_rows(path: Path = PRICE_FILE) -> Iterator[dict[str, Any]]:
    """The same file read as our own articles and names.

    Their name goes to `supplier_name` because the client copies it into the 1C
    comment; ours goes to `own_name` because that is what the shop shows.
    """
    for item in iter_tochka_zreniya_rows(path):
        attributes = item["attributes_json"]
        yield {
            "own_sku": item["supplier_sku"][:64],
            "own_name": item["name"][:512],
            "supplier_marking": None,  # they have no marking of their own
            "supplier_name": (attributes.get("Наименование поставщика") or "")[:512] or None,
            "dealer_price": item["price"],
            "category": (item["supplier_category"] or "")[:255] or None,
            "site_manufacturer": SITE_MANUFACTURER,
            "attributes_json": {"url": item["product_url"]} if item["product_url"] else {},
        }
