"""STZ Salux distributor price list.

The workbook holds 16 sheets and each sheet is a stack of blocks rather than one
table: a group title, a header row, a price sub-header naming the four tiers
(Розница / Опт / Дилер / Дистрибьютор), then the rows, then a gap and the next
block. Column positions shift between sheets, so every block is read through the
header it carries rather than by a fixed offset.

Salux ships no numeric article. The order marking - "ССдП 03-040-004 IP54
\"АЗС 40\"" - is what you quote when ordering, so that is the SKU. The same
marking appears on more than one sheet (an Ex luminaire is listed both in its own
section and among the marine ones), and those repeats are folded into a single
row that remembers every sheet it came from.

Blocks whose header carries no marking column are option lists - dimming, a
longer warranty, an IP upgrade - priced as add-ons to a luminaire rather than
sold on their own. They are counted and skipped.
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
    log_import_error,
    parse_decimal,
    start_import_run,
    flush_product_batch,
)

SUPPLIER_CODE = "salux"
SOURCE_CODE = "price_xlsx"
DEFAULT_XLSX_PATH = r"D:\projects\1C\Салюкс\Прайс_лист_Дистрибьютор_Июнь_2026_Салюкс.xlsx"
BATCH_SIZE = 200

# Which tier the markup rule multiplies. The file is the distributor price list
# and Svetoyar buys at that tier, so it is the base. Changing this changes what
# `price` means for Salux - see docs/pricing_rules.md.
BASE_TIER = "Дистрибьютор"

# The tier sub-header sits under "Стоимость" and normally reads
# Розница / Опт / Дилер / Дистрибьютор. The "ССдО Линия" sheet labels its fourth
# column "Старая Цена" while the numbers follow the same descending ladder, so
# the base falls back to the last tier of the block rather than to the label.

# A row is a block header when it carries one of these.
HEADER_MARKERS = ("Изображение", "Маркировка", "Наименование")
SKU_HEADERS = ("Маркировка для заказа", "Маркировка")
NAME_HEADERS = ("Наименование",)
# Never carried into attributes: either structural or stored in its own column.
ATTR_SKIP = {"Изображение", "Стоимость", *SKU_HEADERS, *NAME_HEADERS}


def _report(progress: Callable[[str], None] | None, message: str) -> None:
    if progress:
        progress(message)
    else:
        print(message, flush=True)


def _norm(value: Any) -> str | None:
    """Collapse the newlines and runs of spaces Excel leaves inside cells."""
    text = clean(value)
    return " ".join(text.split()) if text else None


def _header_map(values: list[Any]) -> dict[str, int] | None:
    """{header text: column index} when this row is a block header."""
    headers: dict[str, int] = {}
    for index, value in enumerate(values):
        text = _norm(value)
        if text and text not in headers:
            headers[text] = index
    if not any(marker in headers for marker in HEADER_MARKERS):
        return None
    if "Стоимость" not in headers:
        return None
    return headers


def _tier_map(values: list[Any], price_column: int) -> dict[str, int]:
    """{tier name: column index} from the sub-header, read left to right.

    Taken by position from the 'Стоимость' column rather than by matching known
    tier names, so a sheet that mislabels one of them still yields its prices.
    """
    tiers: dict[str, int] = {}
    for index in range(price_column, len(values)):
        text = _norm(values[index])
        if not text:
            continue
        if index > price_column and not tiers:
            break  # a gap before the first tier means this is not a tier row
        if text not in tiers:
            tiers[text] = index
    return tiers


def _base_price(prices: dict[str, Decimal], tiers: dict[str, int]) -> tuple[Decimal, str] | None:
    """The tier the markup multiplies, and its name as the sheet spells it."""
    if BASE_TIER in prices:
        return prices[BASE_TIER], BASE_TIER
    for name in reversed(list(tiers)):
        if name in prices:
            return prices[name], name
    return None


def _is_group_title(values: list[Any]) -> bool:
    """A lone caption in the first column, e.g. 'Светильники для АЗС'."""
    filled = [index for index, value in enumerate(values) if _norm(value)]
    return filled == [0]


def _first_present(headers: dict[str, int], candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        if candidate in headers:
            return candidate
    return None


def _cell(values: list[Any], index: int | None) -> Any:
    if index is None or index >= len(values):
        return None
    return values[index]


def _build_attributes(
    values: list[Any],
    headers: dict[str, int],
    *,
    sheet: str,
    group: str | None,
    prices: dict[str, Decimal],
) -> dict[str, Any]:
    attrs: dict[str, Any] = {"sheet": sheet}
    if group:
        attrs["group"] = group
    for header, index in headers.items():
        if header in ATTR_SKIP:
            continue
        text = _norm(_cell(values, index))
        if text:
            attrs[header] = text
    # All four tiers are kept: the client negotiates on them, and only one of
    # them feeds `price`.
    for tier, amount in prices.items():
        attrs[f"price_{tier.lower()}"] = str(amount)
    return attrs


def iter_blocks(
    df: pd.DataFrame, sheet: str
) -> Iterator[tuple[dict[str, int], dict[str, int], str | None, int, list[Any]]]:
    """Yield (headers, tiers, group, row_number, values) for every data row."""
    rows = df.values.tolist()
    group: str | None = None
    headers: dict[str, int] | None = None
    tiers: dict[str, int] = {}
    index = 0

    while index < len(rows):
        values = rows[index]
        found = _header_map(values)
        if found:
            headers = found
            tiers = (
                _tier_map(rows[index + 1], found["Стоимость"])
                if index + 1 < len(rows)
                else {}
            )
            index += 2 if tiers else 1
            continue
        if _is_group_title(values):
            group = _norm(values[0])
            headers = None
            index += 1
            continue
        if headers:
            yield headers, tiers, group, index, values
        index += 1


def normalize_row(
    *,
    sheet: str,
    group: str | None,
    headers: dict[str, int],
    tiers: dict[str, int],
    row_number: int,
    values: list[Any],
    store_raw: bool = False,
) -> dict[str, Any] | None:
    sku_header = _first_present(headers, SKU_HEADERS)
    if sku_header is None:
        return None  # option block, not a sellable product

    sku = _norm(_cell(values, headers[sku_header]))
    if not sku:
        return None

    prices = {}
    for tier, column in tiers.items():
        amount = parse_decimal(_cell(values, column))
        if amount is not None and amount > 0:
            prices[tier] = amount
    chosen = _base_price(prices, tiers)
    if chosen is None:
        return None  # "по запросу" and other rows without a usable price
    base, base_tier = chosen

    name_header = _first_present(headers, NAME_HEADERS)
    name = _norm(_cell(values, headers[name_header])) if name_header else None
    attrs = _build_attributes(values, headers, sheet=sheet, group=group, prices=prices)
    attrs["price_base_tier"] = base_tier

    normalized = {
        "supplier_sku": sku[:128],
        "supplier_sku_raw": sku,
        "name": name or sku,
        "brand": "Салюкс",
        "manufacturer_code": sku[:255],
        "description": None,
        "supplier_category": group or sheet,
        "supplier_category_path": f"{sheet} / {group}" if group else sheet,
        "price": base,
        # Left as the supplier's own retail tier only until the markup rules run
        # at upsert time and overwrite it.
        "price_retail": prices.get("Розница") or base,
        "price_old": None,
        "stock_qty": None,
        "is_available": 1,
        # The series page comes from the site crawl (import_salux_content.py);
        # the price file knows none, and the upsert keeps the crawled one.
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
            "price": str(base),
            "attributes_json": attrs,
        }
    )
    return normalized


def _variant_key(item: dict[str, Any]) -> tuple[str, str, str]:
    """What makes two rows under one marking actually different products."""
    attrs = item["attributes_json"]
    size = next(
        (str(value) for key, value in attrs.items() if key.startswith("Размер корпуса")), ""
    )
    return (str(item["name"] or ""), size, str(item["price"]))


def _discriminator(item: dict[str, Any], field_index: int) -> str:
    attrs = item["attributes_json"]
    size = next(
        (str(value) for key, value in attrs.items() if key.startswith("Размер корпуса")), ""
    )
    return [size, str(item["name"] or ""), str(attrs.get("sheet", ""))][field_index]


def _parse_workbook(
    xlsx_path: Path, *, store_raw: bool
) -> list[dict[str, Any]]:
    # openpyxl is named explicitly rather than inferred: this file reached us
    # misnamed .pdf once already, and the parse should not depend on the name.
    book = pd.ExcelFile(xlsx_path, engine="openpyxl")
    items: list[dict[str, Any]] = []
    for sheet in book.sheet_names:
        df = pd.read_excel(book, sheet_name=sheet, header=None)
        for headers, tiers, group, row_number, values in iter_blocks(df, sheet):
            item = normalize_row(
                sheet=sheet,
                group=group,
                headers=headers,
                tiers=tiers,
                row_number=row_number,
                values=values,
                store_raw=store_raw,
            )
            if item:
                items.append(item)
    return items


def iter_salux_rows(
    xlsx_path: Path,
    *,
    progress: Callable[[str], None] | None = None,
    store_raw: bool = False,
) -> Iterator[dict[str, Any]]:
    """Products across all sheets, one row per distinct product.

    The order marking is not unique in this file, in two different ways:

    * the same product is listed on several sheets - an Ex luminaire appears
      both in its own section and among the marine ones. Those rows are folded
      into one, remembering every sheet they came from.
    * genuinely different products share a marking - "Офис 40" exists as
      595х595х40 and as 1200х180х40 at different prices, both marked
      ``ССдО 03-040-003 IP20 "Офис 40"``. Splitting those apart matters more
      than a tidy SKU, so each variant gets the marking plus the first field
      that tells them apart (size, then name, then sheet).

    Which rows collide is decided over the whole workbook before any SKU is
    issued, so a variant's SKU does not depend on the order sheets are read in.
    """
    items = _parse_workbook(xlsx_path, store_raw=store_raw)

    by_marking: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        by_marking.setdefault(item["supplier_sku"], []).append(item)

    merged = 0
    split = 0

    for marking, group_items in by_marking.items():
        variants: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        for item in group_items:
            variants.setdefault(_variant_key(item), []).append(item)

        for duplicates in variants.values():
            head = duplicates[0]
            sheets = [item["attributes_json"]["sheet"] for item in duplicates]
            if len(sheets) > 1:
                merged += len(sheets) - 1
                head["attributes_json"]["also_on_sheets"] = sheets[1:]

        if len(variants) == 1:
            yield next(iter(variants.values()))[0]
            continue

        # One marking, several products: label them by the first field that
        # separates all of them, and fall back to the sheet name.
        heads = [duplicates[0] for duplicates in variants.values()]
        for field_index in (0, 1, 2):
            labels = [_discriminator(head, field_index) for head in heads]
            if len(set(labels)) == len(labels) and all(labels):
                break
        split += len(heads)
        for head, label in zip(heads, labels):
            head["supplier_sku"] = f"{marking} [{label}]"[:128]
            head["supplier_sku_raw"] = f"{marking} [{label}]"
            head["attributes_json"]["marking"] = marking
            head["attributes_json"]["variant"] = label
            head["content_hash"] = content_hash(
                {
                    "supplier_sku": head["supplier_sku"],
                    "name": head["name"],
                    "price": str(head["price"]),
                    "attributes_json": head["attributes_json"],
                }
            )
            yield head

    if merged:
        _report(progress, f"  {merged} repeated listing(s) folded into their first sheet.")
    if split:
        _report(progress, f"  {split} product(s) share a marking and were split by variant.")


def count_salux_products(xlsx_path: Path) -> int:
    return sum(1 for _ in iter_salux_rows(xlsx_path))


def import_salux_xlsx(
    conn: Connection,
    xlsx_path: Path,
    limit: int | None = None,
    skip_rows: int = 0,
    progress: Callable[[str], None] | None = None,
    store_raw: bool = False,
) -> ImportStats:
    if not xlsx_path.exists():
        raise FileNotFoundError(f"Salux price file not found: {xlsx_path}")

    supplier_id, source_id = get_supplier_and_source(conn, SUPPLIER_CODE, SOURCE_CODE)
    _report(progress, f"Reading Salux workbook ({xlsx_path.name})...")

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
        flush_product_batch(conn, supplier_id, run_id, batch, existing_hashes, stats)
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
        for item in iter_salux_rows(xlsx_path, progress=progress, store_raw=store_raw):
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
