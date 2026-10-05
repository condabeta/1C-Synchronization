#!/usr/bin/env python
"""Compose a product description from its attributes, where the feed gave none.

    python scripts/generate_descriptions.py --dry-run
    python scripts/generate_descriptions.py
    python scripts/generate_descriptions.py --clear   # undo

184,160 of 227,088 products have no description: Dekomo ships one for 11% of its
range, and SWG, Maytoni, ViaSvet and Точка Зрения ship none at all. What they do
ship is structured attributes - material, colour, power, socket, IP, dimensions -
and from those a plain, accurate description can be built. Not marketing copy, but
a real catalogue entry a customer and a search can use.

Only empty descriptions are filled, and each generated one is marked
`description_source = 'generated'`, so a real description is never overwritten and
the whole set can be cleared with --clear. A supplier sending real text later
takes precedence: the importer writes `description`, this leaves it alone.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from staging.db import db_session, fetch_all

BATCH = 1000
MIN_ATTRS = 3  # fewer than this and a description would say almost nothing

# Attributes worth putting in a description, in the order a reader expects them.
# The feed's key is matched by lowercased substring, so one entry covers a
# supplier's variants ("Класс IP", "Степень защиты, IP").
PREFERRED = [
    ("тип лампы", "Тип лампы"), ("тип светильник", "Тип"), ("стиль", "Стиль"),
    ("коллекц", "Коллекция"), ("серия", "Серия"),
    ("материал арматур", "Материал арматуры"), ("материал плафон", "Материал плафона"),
    ("материал", "Материал"), ("основной цвет", "Цвет"), ("цвет арматур", "Цвет арматуры"),
    ("цвет свечени", "Цвет свечения"), ("цвет", "Цвет"),
    ("количество лам", "Количество ламп"), ("общее кол-во лам", "Количество ламп"),
    ("количество плафон", "Количество плафонов"), ("тип цокол", "Цоколь"),
    ("мощность, вт", "Мощность, Вт"), ("мощность", "Мощность"),
    ("цветовая температура", "Цветовая температура, К"), ("световой поток", "Световой поток, Лм"),
    ("класс ip", "Класс защиты IP"), ("степень защиты", "Класс защиты IP"),
    ("напряжени", "Напряжение"), ("питание", "Питание"),
    ("длина, мм", "Длина, мм"), ("ширина, мм", "Ширина, мм"), ("высота, мм", "Высота, мм"),
    ("диаметр", "Диаметр, мм"), ("длина катушки", "Длина катушки, м"),
    ("диапазон рабочих температур", "Рабочая температура"), ("гарантия", "Гарантия"),
    # ViaSvet keeps its attributes under English keys.
    ("power_w", "Мощность, Вт"), ("voltage", "Напряжение"), ("ip_rating", "Класс защиты IP"),
    ("current_a", "Ток, А"), ("dimensions", "Размеры"),
]

# Never put these in a description: stock, pricing, internal bookkeeping, or the
# redundant name/brand/manufacturer fields.
DENY = (
    "склад", "скидк", "активн", "кратн", "аналог", "category", "price", "цена",
    "статус", "единиц", "file", "sheet", "stock", "packaging", "наименовани",
    "производитель", "брутто", "место размещени", "помещени", "запись типов",
    "разрешени", "вес", "id", "guid", "артикул",
)


def clean_value(value: object) -> str | None:
    text = " ".join(str(value).split()) if value is not None else ""
    if text.lower() in ("", "0", "0.0", "0.00", "-", "—", "нет", "0 мм", "none", "null"):
        return None
    return text


def compose(name: str, brand: str, attributes: dict) -> str | None:
    folded = {str(key).lower(): (key, value) for key, value in attributes.items()}
    used_keys: set[str] = set()
    parts: list[str] = []

    def take(label: str, value: str) -> None:
        parts.append(f"{label}: {value}")

    for needle, label in PREFERRED:
        for low, (key, value) in folded.items():
            if key in used_keys or needle not in low:
                continue
            if any(bad in low for bad in DENY):
                continue
            text = clean_value(value)
            if text:
                take(label, text)
                used_keys.add(key)
                break

    if len(parts) < MIN_ATTRS:
        return None

    lead = f"{name} — продукция бренда {brand}." if brand else f"{name}."
    return f"{lead} Характеристики — " + "; ".join(parts[:12]) + "."


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate product descriptions from attributes")
    parser.add_argument("--supplier", action="append", dest="suppliers", metavar="CODE")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--clear", action="store_true", help="remove generated descriptions and exit")
    return parser.parse_args()


def clear(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("UPDATE products SET description = NULL, description_source = NULL "
                    "WHERE description_source = 'generated'")
        n = cur.rowcount
    conn.commit()
    return n


def candidates(conn, suppliers, limit):
    where = ["(p.description IS NULL OR p.description = '')"]
    params: list = []
    if suppliers:
        where.append("s.code IN (" + ", ".join(["%s"] * len(suppliers)) + ")")
        params.extend(suppliers)
    sql = f"""
        SELECT p.id, p.name, p.brand, sp.attributes_json
        FROM products p
        JOIN supplier_products sp ON sp.product_id = p.id AND sp.supplier_id = p.primary_supplier_id
        LEFT JOIN suppliers s ON s.id = p.primary_supplier_id
        WHERE {' AND '.join(where)}
          AND sp.attributes_json IS NOT NULL AND sp.attributes_json <> '{{}}'
        {f'LIMIT {int(limit)}' if limit else ''}
    """
    return fetch_all(conn, sql, params)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
        sys.stderr.reconfigure(encoding="utf-8")
    args = parse_args()

    with db_session() as conn:
        if args.clear:
            print(f"Очищено сгенерированных описаний: {clear(conn):,}")
            return 0

        rows = candidates(conn, args.suppliers, args.limit)
        print(f"Кандидатов без описания: {len(rows):,}")

        made, skipped, batch = 0, 0, []
        for row in rows:
            attributes = row["attributes_json"]
            if not isinstance(attributes, dict):
                attributes = json.loads(attributes)
            text = compose(row["name"] or "", row["brand"] or "", attributes)
            if not text:
                skipped += 1
                continue
            batch.append((text[:65535], row["id"]))
            if not args.dry_run and len(batch) >= BATCH:
                _flush(conn, batch)
                batch.clear()
            made += 1

        if not args.dry_run and batch:
            _flush(conn, batch)

        print(f"Сгенерировано: {made:,} | пропущено (мало данных): {skipped:,}")
        if args.dry_run and rows:
            print("\nПример:")
            for row in rows[:3]:
                attributes = row["attributes_json"]
                if not isinstance(attributes, dict):
                    attributes = json.loads(attributes)
                text = compose(row["name"] or "", row["brand"] or "", attributes)
                if text:
                    print(f"  {text[:300]}")
            print("\nDry run — ничего не записано.")
    return 0


def _flush(conn, batch: list[tuple[str, int]]) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            "UPDATE products SET description = %s, description_source = 'generated' WHERE id = %s",
            batch,
        )
    conn.commit()


if __name__ == "__main__":
    raise SystemExit(main())
