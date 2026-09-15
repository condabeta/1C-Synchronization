"""Svetoyar's own articles and product names.

`светнн1.xlsx` was first read as an eighth supplier, "Свет НН". It is not one -
it is Svetoyar's own price list for goods Salux manufactures. The file says so
itself: its «Производитель» column holds only «Россия» and «Светояр», the string
"Свет НН" appears nowhere, every row carries a Salux order marking, and its
dealer price equals the Salux distributor tier exactly. The client confirmed
this on 2026-09-15.

So it is loaded here rather than through an importer: this is our own data, it
does not belong in `supplier_products`, and it has to survive every supplier
price list reload. Rows link back to the supplier by order marking.

Column order is taken from the header of the first sheet. The second sheet
repeats it without a header, so positions are the contract.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterator

import pandas as pd
from pymysql.connections import Connection

from staging.db import fetch_one
from staging.pricing import as_decimal

DEFAULT_XLSX_PATH = r"D:\projects\1C\Виа Свет\светнн1.xlsx"
# Whose goods these are. Every marking in the file is a Salux one.
SUPPLIER_CODE = "salux"

# Column 1's header text is "Пожаробезопасные светильники" - a section caption
# left in the header row - but its values are the categories, so it is read as
# one.
COLUMNS = (
    "Артикул",
    "Категория",
    "Производитель",
    "Наименование",
    "Мощность, Вт",
    "Световой поток, Лм",
    "Габариты, мм",
    "Маркировка",
    "Цена для дилера",
    "Цена для дилера*1,6",
    "Наше наименование",
    "Степень защиты от пыли/влаги",
    "Напряжение питания сети",
    "Возможность низковольтного исполнения",
)
OWN_SKU = 0
CATEGORY = 1
SUPPLIER_NAME = 3
MARKING = 7
DEALER_PRICE = 8
RETAIL_CHECK = 9
OWN_NAME = 10
# Columns that get their own field rather than being folded into attributes.
STRUCTURAL = {OWN_SKU, CATEGORY, SUPPLIER_NAME, MARKING, DEALER_PRICE, RETAIL_CHECK, OWN_NAME}

EXPECTED_MARKUP = Decimal("1.6")
MARKUP_TOLERANCE = Decimal("0.02")


def _norm(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    return " ".join(text.split())


def _own_sku(value: Any) -> str | None:
    """Articles read as '000001' on one sheet and as 20304.0 on another."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return _norm(value)


def _cell(values: list[Any], index: int) -> Any:
    return values[index] if index < len(values) else None


def normalize_row(values: list[Any], *, sheet: str) -> dict[str, Any] | None:
    own_sku = _own_sku(_cell(values, OWN_SKU))
    if not own_sku:
        return None  # category caption rows carry no article

    price = as_decimal(_cell(values, DEALER_PRICE))
    if price is not None and price <= 0:
        price = None

    attrs: dict[str, Any] = {"sheet": sheet}
    for index, header in enumerate(COLUMNS):
        if index in STRUCTURAL:
            continue
        text = _norm(_cell(values, index))
        if text:
            attrs[header] = text

    return {
        "own_sku": own_sku[:64],
        "own_name": (_norm(_cell(values, OWN_NAME)) or "")[:512] or None,
        "supplier_marking": (_norm(_cell(values, MARKING)) or "")[:255] or None,
        "supplier_name": (_norm(_cell(values, SUPPLIER_NAME)) or "")[:512] or None,
        "dealer_price": price,
        "category": (_norm(_cell(values, CATEGORY)) or "")[:255] or None,
        "attributes_json": attrs,
    }


def markup_gap(values: list[Any], price: Decimal | None) -> Decimal | None:
    """Distance between the file's own x1.6 column and our arithmetic."""
    stated = as_decimal(_cell(values, RETAIL_CHECK))
    if stated is None or price is None:
        return None
    return abs(stated - price * EXPECTED_MARKUP)


def iter_own_articles(
    xlsx_path: Path, *, progress: Callable[[str], None] | None = None
) -> Iterator[dict[str, Any]]:
    book = pd.ExcelFile(xlsx_path, engine="openpyxl")
    seen: set[str] = set()
    checked = mismatched = 0

    for sheet in book.sheet_names:
        # The second sheet stops at column K while the first runs to N, and the
        # first reports thousands of empty trailing columns.
        width = min(len(COLUMNS), book.book[sheet].max_column)
        frame = pd.read_excel(book, sheet_name=sheet, header=None, usecols=range(width))
        for row in frame.values.tolist():
            values = list(row)
            if _norm(_cell(values, OWN_SKU)) == "Артикул":
                continue
            item = normalize_row(values, sheet=sheet)
            if not item or item["own_sku"] in seen:
                continue
            seen.add(item["own_sku"])

            gap = markup_gap(values, item["dealer_price"])
            if gap is not None:
                checked += 1
                if gap > MARKUP_TOLERANCE:
                    mismatched += 1
            yield item

    if checked and progress:
        progress(
            f"  Проверка x1.6: {checked - mismatched:,} из {checked:,} строк сходятся "
            "с колонкой «Цена для дилера*1,6»."
        )


UPSERT_SQL = """
    INSERT INTO own_articles (
        own_sku, own_name, supplier_id, supplier_marking, supplier_name,
        dealer_price, category, attributes_json, source_file
    ) VALUES (
        %(own_sku)s, %(own_name)s, %(supplier_id)s, %(supplier_marking)s, %(supplier_name)s,
        %(dealer_price)s, %(category)s, %(attributes_json)s, %(source_file)s
    )
    ON DUPLICATE KEY UPDATE
        own_name = VALUES(own_name),
        supplier_id = VALUES(supplier_id),
        supplier_marking = VALUES(supplier_marking),
        supplier_name = VALUES(supplier_name),
        dealer_price = VALUES(dealer_price),
        category = VALUES(category),
        attributes_json = VALUES(attributes_json),
        source_file = VALUES(source_file)
"""


def load_own_articles(
    conn: Connection, xlsx_path: Path, *, progress: Callable[[str], None] | None = None
) -> int:
    if not xlsx_path.exists():
        raise FileNotFoundError(f"Own price list not found: {xlsx_path}")

    supplier = fetch_one(conn, "SELECT id FROM suppliers WHERE code = %s", (SUPPLIER_CODE,))
    supplier_id = supplier["id"] if supplier else None

    rows = []
    for item in iter_own_articles(xlsx_path, progress=progress):
        rows.append(
            {
                **item,
                "supplier_id": supplier_id,
                "attributes_json": json.dumps(item["attributes_json"], ensure_ascii=False),
                "source_file": xlsx_path.name[:255],
            }
        )

    if rows:
        with conn.cursor() as cur:
            cur.executemany(UPSERT_SQL, rows)
        conn.commit()
    return len(rows)


def match_report(conn: Connection) -> dict[str, int]:
    """How much of the supplier's catalogue our own articles cover."""
    row = fetch_one(
        conn,
        """
        SELECT
          (SELECT COUNT(*) FROM own_articles) AS own_total,
          (SELECT COUNT(*) FROM own_articles oa
             WHERE EXISTS (SELECT 1 FROM supplier_products sp
                           WHERE sp.supplier_id = oa.supplier_id
                             AND sp.supplier_sku LIKE CONCAT(oa.supplier_marking, '%%'))
          ) AS own_matched,
          (SELECT COUNT(*) FROM supplier_products sp
             JOIN suppliers s ON s.id = sp.supplier_id AND s.code = %s) AS supplier_total,
          (SELECT COUNT(*) FROM supplier_products sp
             JOIN suppliers s ON s.id = sp.supplier_id AND s.code = %s
             WHERE EXISTS (SELECT 1 FROM own_articles oa
                           WHERE sp.supplier_sku LIKE CONCAT(oa.supplier_marking, '%%'))
          ) AS supplier_covered
        """,
        (SUPPLIER_CODE, SUPPLIER_CODE),
    )
    return {k: int(v or 0) for k, v in (row or {}).items()}
