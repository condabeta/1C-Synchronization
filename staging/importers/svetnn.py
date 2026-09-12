"""Svet NN price list.

One flat table per sheet, unlike the block layout of the Salux workbook. The
first sheet carries a header row and the rest repeat its column order without
one, so columns are read by position with the header used only to confirm the
layout has not shifted.

Svet NN resells Salux hardware - the "Маркировка" column holds the same
``ССдВз 01-010-030 IP65`` order markings that appear in the Salux price list -
so the two suppliers overlap and the catalogue will need to merge them.

The file already contains the retail column the client asked for, "Цена для
дилера*1,6". We do not import it: the markup rules compute retail from the
dealer price, and that column is used to check the computed value instead.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterator

import pandas as pd
from pymysql.connections import Connection

from staging.importers.common import (
    ImportStats,
    clean,
    close_stale_runs,
    content_hash,
    count_supplier_products,
    file_sha256,
    finish_import_run,
    get_supplier_and_source,
    load_existing_hashes,
    log_import_error,
    parse_decimal,
    start_import_run,
    upsert_product_batch,
)

SUPPLIER_CODE = "svetnn"
SOURCE_CODE = "price_xlsx"
DEFAULT_XLSX_PATH = r"D:\projects\1C\Виа Свет\светнн1.pdf"
BATCH_SIZE = 200

# Column order, taken from the header of the first sheet. Sheets without a
# header repeat it, so positions are the contract and the header is only a check.
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
SKU_COLUMN = 0
PRICE_COLUMN = 8
RETAIL_CHECK_COLUMN = 9
# Columns kept as their own field rather than folded into attributes.
STRUCTURAL = {0, 1, 3, 8, 9}

# What the supplier's own retail column should equal. Used to verify the markup
# rule, never to set a price.
EXPECTED_MARKUP = Decimal("1.6")
MARKUP_TOLERANCE = Decimal("0.02")


def _report(progress: Callable[[str], None] | None, message: str) -> None:
    if progress:
        progress(message)
    else:
        print(message, flush=True)


def _norm(value: Any) -> str | None:
    text = clean(value)
    return " ".join(text.split()) if text else None


def _sku(value: Any) -> str | None:
    """Articles read as '000001' on one sheet and as 20304.0 on another."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return _norm(value)


def _is_header_row(values: list[Any]) -> bool:
    return _norm(_cell(values, SKU_COLUMN)) == "Артикул"


def _cell(values: list[Any], index: int) -> Any:
    return values[index] if index < len(values) else None


def normalize_row(
    values: list[Any],
    *,
    sheet: str,
    row_number: int,
    store_raw: bool = False,
) -> dict[str, Any] | None:
    sku = _sku(_cell(values, SKU_COLUMN))
    if not sku:
        return None  # category caption rows carry no article

    price = parse_decimal(_cell(values, PRICE_COLUMN))
    if price is None or price <= 0:
        return None

    category = _norm(_cell(values, 1))
    name = _norm(_cell(values, 3))
    our_name = _norm(_cell(values, 10))

    attrs: dict[str, Any] = {"sheet": sheet}
    for index, header in enumerate(COLUMNS):
        if index in STRUCTURAL:
            continue
        text = _norm(_cell(values, index))
        if text:
            attrs[header] = text

    normalized = {
        "supplier_sku": sku[:128],
        "supplier_sku_raw": sku,
        "name": name or our_name or sku,
        "brand": "Свет НН",
        # The order marking, shared with the Salux price list - this is what
        # lets the two suppliers be matched to one product.
        "manufacturer_code": (_norm(_cell(values, 7)) or "")[:255] or None,
        "description": None,
        "supplier_category": category,
        "supplier_category_path": category,
        "price": price,
        "price_retail": price,  # replaced by the markup rules at upsert time
        "price_old": None,
        "stock_qty": None,
        "is_available": 1,
        "product_url": None,
        "barcode": None,
        "images_json": [],
        "attributes_json": attrs,
        "raw_data_json": {"sheet": sheet, "row_number": row_number,
                          "values": [_norm(v) for v in values]}
        if store_raw
        else None,
        "source_row_number": row_number,
    }
    normalized["content_hash"] = content_hash(
        {
            "supplier_sku": normalized["supplier_sku"],
            "name": normalized["name"],
            "price": str(price),
            "attributes_json": attrs,
        }
    )
    return normalized


def check_markup(values: list[Any], price: Decimal) -> Decimal | None:
    """The gap between the supplier's own x1.6 column and our own arithmetic."""
    stated = parse_decimal(_cell(values, RETAIL_CHECK_COLUMN))
    if stated is None:
        return None
    return abs(stated - price * EXPECTED_MARKUP)


def iter_svetnn_rows(
    xlsx_path: Path,
    *,
    progress: Callable[[str], None] | None = None,
    store_raw: bool = False,
) -> Iterator[dict[str, Any]]:
    # openpyxl is named explicitly: the file arrives with a .pdf extension.
    book = pd.ExcelFile(xlsx_path, engine="openpyxl")
    seen: set[str] = set()
    mismatches = 0
    checked = 0

    for sheet in book.sheet_names:
        # The second sheet stops at column K while the first runs to N - and the
        # first also reports thousands of empty trailing columns, so the read is
        # clamped to the columns that exist and that we use.
        width = min(len(COLUMNS), book.book[sheet].max_column)
        df = pd.read_excel(book, sheet_name=sheet, header=None, usecols=range(width))
        for row_number, row in enumerate(df.values.tolist()):
            values = list(row)
            if _is_header_row(values):
                continue
            item = normalize_row(
                values, sheet=sheet, row_number=row_number, store_raw=store_raw
            )
            if not item:
                continue
            if item["supplier_sku"] in seen:
                _report(progress, f"  Warning: duplicate article {item['supplier_sku']} on {sheet}")
                continue
            seen.add(item["supplier_sku"])

            gap = check_markup(values, item["price"])
            if gap is not None:
                checked += 1
                if gap > MARKUP_TOLERANCE:
                    mismatches += 1
                    if mismatches <= 5:
                        _report(
                            progress,
                            f"  Warning: {item['supplier_sku']} - supplier's x1.6 column is "
                            f"off ours by {gap}",
                        )
            yield item

    if checked:
        _report(
            progress,
            f"  Markup check: {checked - mismatches:,}/{checked:,} rows match the supplier's "
            f"own x1.6 column.",
        )


def count_svetnn_products(xlsx_path: Path) -> int:
    return sum(1 for _ in iter_svetnn_rows(xlsx_path, progress=lambda _msg: None))


def import_svetnn_xlsx(
    conn: Connection,
    xlsx_path: Path,
    limit: int | None = None,
    skip_rows: int = 0,
    progress: Callable[[str], None] | None = None,
    store_raw: bool = False,
) -> ImportStats:
    if not xlsx_path.exists():
        raise FileNotFoundError(f"Svet NN price file not found: {xlsx_path}")

    supplier_id, source_id = get_supplier_and_source(conn, SUPPLIER_CODE, SOURCE_CODE)
    _report(progress, f"Reading Svet NN workbook ({xlsx_path.name})...")

    close_stale_runs(conn, supplier_id)
    source_hash = file_sha256(xlsx_path)
    run_id = start_import_run(conn, supplier_id, source_id, str(xlsx_path), source_hash)
    conn.commit()

    stats = ImportStats()
    batch: list[dict[str, Any]] = []
    existing_hashes: dict[str, str] = {}
    batch_number = 0
    product_index = 0

    def flush() -> None:
        nonlocal batch_number
        if not batch:
            return
        batch_number += 1
        skus = [row["supplier_sku"] for row in batch]
        existing_hashes.update(load_existing_hashes(conn, supplier_id, skus))
        upsert_product_batch(conn, supplier_id, run_id, batch, existing_hashes, stats)
        conn.commit()
        batch.clear()
        _report(
            progress,
            (
                f"  batch {batch_number}: products {product_index:,} | "
                f"db {count_supplier_products(conn, supplier_id):,} | "
                f"new {stats.rows_imported:,} | updated {stats.rows_updated:,} | "
                f"unchanged {stats.rows_skipped:,} | errors {stats.rows_errors:,}"
            ),
        )

    try:
        for item in iter_svetnn_rows(xlsx_path, progress=progress, store_raw=store_raw):
            stats.rows_total += 1
            try:
                product_index += 1
                if product_index <= skip_rows:
                    continue
                if limit is not None and product_index - skip_rows > limit:
                    break

                batch.append(item)
                if len(batch) >= BATCH_SIZE:
                    flush()
            except Exception as exc:
                stats.rows_errors += 1
                stats.errors.append(
                    {
                        "row": item.get("source_row_number"),
                        "sku": item.get("supplier_sku"),
                        "error": str(exc),
                    }
                )
                log_import_error(
                    conn,
                    run_id,
                    item.get("source_row_number"),
                    item.get("supplier_sku"),
                    str(exc),
                    item,
                )
                conn.commit()

        flush()
        status = "success" if not stats.rows_errors else "partial"
        finish_import_run(conn, run_id, stats, status)
        conn.commit()
    except Exception as exc:
        finish_import_run(conn, run_id, stats, "failed", str(exc))
        conn.commit()
        raise

    return stats
