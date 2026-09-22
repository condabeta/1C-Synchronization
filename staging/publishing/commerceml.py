"""CommerceML 2 export: the catalogue as 1C expects to receive it.

1C Fresh УНФ exchanges over CommerceML. In that protocol **1C is the client and
the site is the server**: 1C connects to an address on the site, authenticates,
and asks for or sends files. So two halves are needed, and this is the first -
turning the catalogue into the files 1C reads:

* `import.xml` - «Классификатор» (the category tree, units, properties) and
  «Каталог» (the products themselves).
* `offers.xml` - «ПакетПредложений» (price types, prices, stock).

Both are built from `products` and `categories`, for products that have passed
moderation. Everything is identified by «Ид», the GUID stored on the row: 1C
keys its own records on it, so a product that arrives with a new GUID becomes a
second product in 1C rather than an update of the first.

The second half, the HTTP endpoint 1C connects to, is `staging/web/exchange.py`.
It needs the paid УНФ tariff to test against, which the client does not have
yet; the file writing here is testable on its own and is what that endpoint
serves.

Reference: CommerceML 2.03, the schema 1C's «Обмен с сайтом» writes and reads.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from pymysql.connections import Connection

from staging.db import fetch_all, fetch_one

SCHEMA_VERSION = "2.03"
DEFAULT_UNIT = ("796", "шт", "Штука")

# Only what a moderator has passed. 'archived' and 'rejected' never go out, and
# neither does anything still waiting for review.
PUBLISHABLE_STATUSES = ("approved", "synced_1c", "published")

CATEGORY_SQL = """
    SELECT c.id, c.parent_id, c.name, c.slug, c.onec_guid, c.sort_order
    FROM categories c
    WHERE c.is_active = 1
    ORDER BY c.sort_order
"""

PRODUCT_SQL = f"""
    SELECT p.id, p.internal_sku, p.onec_guid, p.onec_code, p.name, p.brand, p.description,
           p.barcode, p.unit, p.okei_code, p.weight, p.vat_rate, p.category_id,
           p.price, p.price_retail, p.stock_qty, p.is_available, p.slug,
           c.onec_guid AS category_guid,
           sp.supplier_sku AS supplier_marking, s.name AS supplier_name
    FROM products p
    LEFT JOIN categories c ON c.id = p.category_id
    -- The supplier's own article travels with the product: 1C keys on our
    -- number, and a manager ordering from the supplier needs theirs.
    LEFT JOIN supplier_products sp ON sp.product_id = p.id
                                  AND sp.supplier_id = p.primary_supplier_id
    LEFT JOIN suppliers s ON s.id = p.primary_supplier_id
    WHERE p.status IN ({', '.join(['%s'] * len(PUBLISHABLE_STATUSES))})
      AND p.onec_guid IS NOT NULL
    ORDER BY p.id
"""

IMAGE_SQL = """
    SELECT product_id, source_path, image_type
    FROM product_images
    WHERE product_id IN ({placeholders})
    ORDER BY product_id, image_type = 'main' DESC, sort_order
"""


@dataclass
class ExportStats:
    categories: int = 0
    products: int = 0
    offers: int = 0
    skipped_no_category: int = 0
    skipped_no_price: int = 0
    files: list[Path] = field(default_factory=list)


def _sub(parent: ET.Element, tag: str, text: Any = None) -> ET.Element:
    node = ET.SubElement(parent, tag)
    if text is not None:
        node.text = str(text)
    return node


def _now() -> tuple[str, str]:
    moment = datetime.now()
    return moment.strftime("%Y-%m-%d"), moment.strftime("%H:%M:%S")


def _root(tag_date: str, tag_time: str) -> ET.Element:
    root = ET.Element("КоммерческаяИнформация")
    root.set("ВерсияСхемы", SCHEMA_VERSION)
    root.set("ДатаФормирования", f"{tag_date}T{tag_time}")
    return root


# ---------------------------------------------------------------------------
# Классификатор + Каталог
# ---------------------------------------------------------------------------


def _groups(parent: ET.Element, categories: list[dict[str, Any]]) -> None:
    """The category tree, nested the way CommerceML wants it."""
    by_parent: dict[int | None, list[dict[str, Any]]] = {}
    for row in categories:
        by_parent.setdefault(row["parent_id"], []).append(row)

    def emit(container: ET.Element, parent_id: int | None) -> None:
        for row in by_parent.get(parent_id, []):
            group = _sub(container, "Группа")
            _sub(group, "Ид", row["onec_guid"])
            _sub(group, "Наименование", row["name"])
            children = by_parent.get(row["id"])
            if children:
                emit(_sub(group, "Группы"), row["id"])

    emit(_sub(parent, "Группы"), None)


def build_classifier(classifier_guid: str, categories: list[dict[str, Any]]) -> ET.Element:
    classifier = ET.Element("Классификатор")
    _sub(classifier, "Ид", classifier_guid)
    _sub(classifier, "Наименование", "Классификатор Светояр")
    _groups(classifier, categories)

    units = _sub(classifier, "ЕдиницыИзмерения")
    code, short, full = DEFAULT_UNIT
    unit = _sub(units, "ЕдиницаИзмерения")
    _sub(unit, "Код", code)
    _sub(unit, "НаименованиеПолное", full)
    _sub(unit, "МеждународноеСокращение", short)
    return classifier


def _product_node(parent: ET.Element, row: dict[str, Any], images: list[str]) -> None:
    node = _sub(parent, "Товар")
    _sub(node, "Ид", row["onec_guid"])
    _sub(node, "Артикул", row["onec_code"] or row["internal_sku"])
    _sub(node, "Наименование", row["name"])

    if row["category_guid"]:
        groups = _sub(node, "Группы")
        _sub(groups, "Ид", row["category_guid"])

    if row["description"]:
        _sub(node, "Описание", row["description"])
    if row["barcode"]:
        _sub(node, "Штрихкод", row["barcode"])

    unit = _sub(node, "БазоваяЕдиница", row["unit"] or DEFAULT_UNIT[1])
    unit.set("Код", row["okei_code"] or DEFAULT_UNIT[0])
    unit.set("НаименованиеПолное", DEFAULT_UNIT[2])

    for image in images[:10]:  # 1C ignores the rest; keep the payload sane
        _sub(node, "Картинка", image)

    details = _sub(node, "ЗначенияРеквизитов")
    # Салюкс is the case that made this necessary: in 1C the article is ours
    # ("000001") and the supplier's marking lives in a comment beside it
    # (Юлия, 22.09.2026). Sending only one of the two loses the other.
    marking = row.get("supplier_marking")
    if marking and marking != (row["onec_code"] or row["internal_sku"]):
        marking = f"{row['supplier_name']}: {marking}" if row.get("supplier_name") else marking
    else:
        marking = None
    for name, value in (
        ("Полное наименование", row["name"]),
        ("Бренд", row["brand"]),
        ("Вес", row["weight"]),
        ("Ссылка на сайт", row["slug"]),
        ("Маркировка поставщика", marking),
    ):
        if value in (None, ""):
            continue
        detail = _sub(details, "ЗначениеРеквизита")
        _sub(detail, "Наименование", name)
        _sub(detail, "Значение", value)


def build_catalog(
    catalog_guid: str,
    classifier_guid: str,
    products: list[dict[str, Any]],
    images: dict[int, list[str]],
) -> ET.Element:
    catalog = ET.Element("Каталог")
    catalog.set("СодержитТолькоИзменения", "false")
    _sub(catalog, "Ид", catalog_guid)
    _sub(catalog, "ИдКлассификатора", classifier_guid)
    _sub(catalog, "Наименование", "Каталог товаров Светояр")
    container = _sub(catalog, "Товары")
    for row in products:
        _product_node(container, row, images.get(row["id"], []))
    return catalog


# ---------------------------------------------------------------------------
# ПакетПредложений
# ---------------------------------------------------------------------------


def build_offers(
    package_guid: str,
    classifier_guid: str,
    price_types: list[dict[str, Any]],
    products: list[dict[str, Any]],
) -> tuple[ET.Element, int]:
    package = ET.Element("ПакетПредложений")
    _sub(package, "Ид", package_guid)
    _sub(package, "Наименование", "Предложения Светояр")
    _sub(package, "ИдКаталога", classifier_guid)

    types_node = _sub(package, "ТипыЦен")
    for price_type in price_types:
        node = _sub(types_node, "ТипЦены")
        _sub(node, "Ид", price_type["onec_guid"])
        _sub(node, "Наименование", price_type["name"])
        _sub(node, "Валюта", price_type["currency"])

    offers = _sub(package, "Предложения")
    written = 0
    for row in products:
        prices = [
            (price_type, row.get(price_type["source_field"]))
            for price_type in price_types
        ]
        if not any(value is not None for _, value in prices):
            continue

        offer = _sub(offers, "Предложение")
        _sub(offer, "Ид", row["onec_guid"])
        _sub(offer, "Артикул", row["onec_code"] or row["internal_sku"])
        _sub(offer, "Наименование", row["name"])

        unit = _sub(offer, "БазоваяЕдиница", row["unit"] or DEFAULT_UNIT[1])
        unit.set("Код", row["okei_code"] or DEFAULT_UNIT[0])

        price_node = _sub(offer, "Цены")
        for price_type, value in prices:
            if value is None:
                continue
            price = _sub(price_node, "Цена")
            _sub(price, "ИдТипаЦены", price_type["onec_guid"])
            _sub(price, "ЦенаЗаЕдиницу", f"{value:.2f}")
            _sub(price, "Валюта", price_type["currency"])
            _sub(price, "Единица", row["unit"] or DEFAULT_UNIT[1])
            _sub(price, "Коэффициент", "1")

        # Stock. A product with no stock figure is offered as zero rather than
        # left silent: silence reads as "unchanged" to 1C.
        _sub(offer, "Количество", f"{row['stock_qty']:.3f}" if row["stock_qty"] is not None else "0")
        written += 1

    return package, written


# ---------------------------------------------------------------------------
# Writing it out
# ---------------------------------------------------------------------------


def _write(root: ET.Element, path: Path) -> Path:
    ET.indent(root, space="  ")
    path.parent.mkdir(parents=True, exist_ok=True)
    # windows-1251 is what 1C's own exchange writes; it reads utf-8 too, and
    # utf-8 is what the rest of this system speaks, so nothing is transcoded.
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return path


def load_products(conn: Connection, limit: int | None = None) -> list[dict[str, Any]]:
    sql = PRODUCT_SQL + (f" LIMIT {int(limit)}" if limit else "")
    return fetch_all(conn, sql, PUBLISHABLE_STATUSES)


def load_images(conn: Connection, product_ids: Iterable[int]) -> dict[int, list[str]]:
    ids = list(product_ids)
    if not ids:
        return {}
    images: dict[int, list[str]] = {}
    chunk = 5000
    for start in range(0, len(ids), chunk):
        part = ids[start : start + chunk]
        rows = fetch_all(
            conn,
            IMAGE_SQL.format(placeholders=", ".join(["%s"] * len(part))),
            part,
        )
        for row in rows:
            images.setdefault(row["product_id"], []).append(row["source_path"])
    return images


def export(conn: Connection, out_dir: Path, *, limit: int | None = None) -> ExportStats:
    """Write import.xml and offers.xml. Returns what went into them."""
    stats = ExportStats()
    tag_date, tag_time = _now()

    categories = fetch_all(conn, CATEGORY_SQL)
    missing_guid = [row["slug"] for row in categories if not row["onec_guid"]]
    if missing_guid:
        raise ValueError(
            "Categories without a GUID: " + ", ".join(missing_guid[:5])
            + " - run scripts/prepare_publishing.py first"
        )
    stats.categories = len(categories)

    products = load_products(conn, limit=limit)
    stats.products = len(products)
    stats.skipped_no_category = sum(1 for row in products if not row["category_guid"])

    price_types = fetch_all(
        conn, "SELECT code, name, onec_guid, source_field, currency FROM price_types WHERE is_active = 1"
    )
    images = load_images(conn, (row["id"] for row in products))

    classifier_guid, catalog_guid = CLASSIFIER_GUID, CATALOG_GUID

    root = _root(tag_date, tag_time)
    root.append(build_classifier(classifier_guid, categories))
    root.append(build_catalog(catalog_guid, classifier_guid, products, images))
    stats.files.append(_write(root, out_dir / "import.xml"))

    offers_root = _root(tag_date, tag_time)
    package, written = build_offers(OFFERS_GUID, classifier_guid, price_types, products)
    offers_root.append(package)
    stats.offers = written
    stats.skipped_no_price = len(products) - written
    stats.files.append(_write(offers_root, out_dir / "offers.xml"))

    return stats


# Fixed for the life of the catalogue: 1C ties its own classifier, catalogue and
# offer package to these, and a new value means a new set of everything.
CLASSIFIER_GUID = "0f2b1a90-7c4d-4e5f-8a1b-2c3d4e5f6a70"
CATALOG_GUID = "0f2b1a90-7c4d-4e5f-8a1b-2c3d4e5f6a71"
OFFERS_GUID = "0f2b1a90-7c4d-4e5f-8a1b-2c3d4e5f6a72"
