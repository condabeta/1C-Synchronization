"""The site's own category tree, and how supplier sections map into it.

Every supplier names its sections differently, and none of them names them the
way a shop's menu should read. Dekomo sends a set of types per product
("Потолочные светильники,Подвесные люстры"), Arlight a department and a series
("Светодиодные ленты / Малый шаг резки / X360 5V 8mm"), Salux a family code
(ССдВз 1Ех), Jazzway numbered sections, SWG a flat list, Crystal and ViaSvet a
sheet name. 4,000 distinct section names in total, for 219,000 products.

Rather than curate 4,000 mappings by hand or guess with a parser, the tree here
is fixed and each node claims supplier sections by keyword. A table of rules can
be read, argued with and corrected by whoever knows the catalogue; the mapping
it produces is written to `supplier_category_map`, which is what the rest of the
system reads, so a wrong rule is a row to fix, not a re-import.

Matching runs against the whole supplier path, folded (lowercased, Latin
lookalikes turned into Cyrillic), and the first rule that matches wins - so the
rules are ordered from most specific to most general. "Потолочные светильники /
Подвесные люстры" must reach Люстры rather than Потолочные, which is why
Люстры comes first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from pymysql.connections import Connection

from staging.db import fetch_all, fetch_one
from staging.pricing import fold

# ---------------------------------------------------------------------------
# The tree
# ---------------------------------------------------------------------------

# (slug, name, parent slug). Kept flat and shallow on purpose: two levels are
# what a shop menu can show, and the supplier's own path stays on the product
# for anyone who needs the detail.
TAXONOMY: list[tuple[str, str, str | None]] = [
    ("svetilniki", "Светильники", None),
    ("lyustry", "Люстры", "svetilniki"),
    ("podvesnye", "Подвесные светильники", "svetilniki"),
    ("potolochnye", "Потолочные и накладные", "svetilniki"),
    ("vstraivaemye", "Встраиваемые и точечные", "svetilniki"),
    ("spoty", "Споты и трековые системы", "svetilniki"),
    ("bra", "Бра и настенные", "svetilniki"),
    ("nastolnye", "Настольные лампы", "svetilniki"),
    ("torshery", "Торшеры", "svetilniki"),
    ("mebelnaya-podsvetka", "Мебельная подсветка", "svetilniki"),

    ("ulichnye", "Уличное и ландшафтное освещение", None),
    ("ulichnye-nastennye", "Уличные настенные", "ulichnye"),
    ("ulichnye-nazemnye", "Наземные и парковые", "ulichnye"),
    ("ulichnye-prozhektory", "Прожекторы", "ulichnye"),

    ("tehnicheskie", "Технические светильники", None),
    ("promyshlennye", "Промышленные", "tehnicheskie"),
    ("ofisnye", "Офисные и для ЖКХ", "tehnicheskie"),
    ("vzryvozashchita", "Взрывозащищённые", "tehnicheskie"),
    ("sudovye", "Судовые", "tehnicheskie"),
    ("azs", "АЗС и специальные", "tehnicheskie"),

    ("lenta", "Светодиодная лента и неон", None),
    ("lenta-lenty", "Ленты", "lenta"),
    ("lenta-neon", "Гибкий неон", "lenta"),
    ("lenta-profil", "Профили", "lenta"),
    ("lenta-aksessuary", "Комплектующие для лент и профилей", "lenta"),

    ("pitanie", "Питание и управление", None),
    ("bloki-pitaniya", "Блоки питания и трансформаторы", "pitanie"),
    ("upravlenie", "Контроллеры, диммеры, датчики", "pitanie"),

    ("lampy", "Лампы", None),
    ("elementy-pitaniya", "Элементы питания и фонари", None),
    ("elektroustanovka", "Электроустановочные изделия", None),
    ("komplektuyushchie", "Комплектующие и аксессуары", None),
    ("mebel", "Мебель и товары для дома", None),
    ("prochee", "Прочее", None),
]


@dataclass(frozen=True)
class CategoryRule:
    """A node's claim on supplier sections, by keyword."""

    slug: str
    keywords: tuple[str, ...]

    def matches(self, folded_path: str) -> str | None:
        for keyword in self.keywords:
            if fold(keyword) in folded_path:
                return keyword
        return None


def _rule(slug: str, *keywords: str) -> CategoryRule:
    return CategoryRule(slug, keywords)


# Order is the priority. The first match wins, so anything that would otherwise
# be swallowed by a broader word has to come before it.
RULES: list[CategoryRule] = [
    # Families that name themselves. Salux markings and the explosion-proof and
    # marine lines must not be read as ordinary ceiling lights.
    _rule("vzryvozashchita", "взрывозащищ", "ссдвз", "1ех", "ex комплектующие"),
    _rule("sudovye", "судов", "ссдс"),
    _rule("azs", "азс", "clean room", "ссдо специальные"),
    _rule("ofisnye", "офис", "жкх", "ссдо"),
    _rule("promyshlennye", "промышленн", "ссдп", "ссдпб", "складск", "высокий пролёт", "высокий пролет"),

    # Tape, neon and profile before anything that merely says "светильник".
    _rule("lenta-neon", "неон"),
    _rule("lenta-profil", "профил"),
    _rule("lenta-aksessuary", "заглушк", "для лент", "комплектующие для лент", "коннектор", "соединител"),
    _rule("lenta-lenty", "лент"),

    _rule("bloki-pitaniya", "блок питания", "блоки питания", "источник питания", "источники питания",
          "трансформатор", "драйвер", "бп,"),
    _rule("upravlenie", "контроллер", "диммер", "датчик", "пульт", "управлени", "автоматизац", "реле"),

    _rule("elementy-pitaniya", "элементы питания", "батаре", "аккумулятор", "зарядн", "фонарь", "фонари"),
    _rule("elektroustanovka", "розетк", "выключател", "рамк", "электроустановочн", "кабел", "провод",
          "удлинител", "щит", "подрозетник", "терморегулятор", "нагревательн", "вилки", "термоусадочн",
          "автомат", "клемм"),

    # Luminaire shapes. Люстры first: a chandelier's path almost always also
    # says "потолочные светильники".
    _rule("lyustry", "люстр"),
    _rule("spoty", "спот", "треков", "магнитн", "шинопровод"),
    _rule("ulichnye-prozhektory", "прожектор"),
    _rule("ulichnye-nazemnye", "наземн", "фонарн", "парков", "грунтов", "ландшафтн", "садов"),
    _rule("ulichnye-nastennye", "уличн", "фасадн"),
    _rule("mebelnaya-podsvetka", "мебельн", "подсветка для", "подсветк"),
    _rule("nastolnye", "настольн"),
    _rule("torshery", "торшер"),
    _rule("bra", "бра", "настенн"),
    _rule("vstraivaemye", "встраиваем", "точечн", "даунлайт", "карданн"),
    _rule("podvesnye", "подвесн"),
    _rule("potolochnye", "накладн", "потолочн", "настенно-потолочн"),

    _rule("lampy", "ламп"),
    _rule("mebel", "мебел", "матрас", "ковер", "ковр", "кроват", "стол", "стул", "кресл", "диван",
          "шкаф", "комод", "тумб", "текстиль", "посуд", "зеркал", "полк", "пуф"),
    _rule("komplektuyushchie", "комплектующ", "аксессуар", "основани", "креплен", "монтаж", "корпус",
          "рассеиват", "модул", "радиатор"),
    _rule("svetilniki", "светильник", "свет"),
    # Dekomo is a lighting *and* home-goods distributor: vases, artificial
    # plants, room scents, tableware. Small in number, and nothing in the tree
    # above wants them.
    _rule("prochee", "декор", "принадлежности", "скульптур", "статуэтк", "ароматизатор",
          "искусственные растения", "сопутствующие товары"),
]

# Sections that carry no meaning of their own; the products under them are
# classified by name instead, or left for review.
IGNORED_SECTIONS = ("sale", "распродажа", "акция", "новинки", "рекламные материалы")


# ---------------------------------------------------------------------------
# Building the tree
# ---------------------------------------------------------------------------


def build_tree(conn: Connection) -> dict[str, int]:
    """Create or refresh the category rows. Returns {slug: category_id}."""
    ids: dict[str, int] = {}
    with conn.cursor() as cur:
        for order, (slug, name, parent) in enumerate(TAXONOMY, start=1):
            parent_id = ids.get(parent) if parent else None
            path = f"/{parent}/{slug}/" if parent else f"/{slug}/"
            existing = fetch_one(conn, "SELECT id FROM categories WHERE slug = %s", (slug,))
            if existing:
                cur.execute(
                    """
                    UPDATE categories SET name = %s, parent_id = %s, path = %s, sort_order = %s
                    WHERE id = %s
                    """,
                    (name, parent_id, path, order, existing["id"]),
                )
                ids[slug] = existing["id"]
            else:
                cur.execute(
                    """
                    INSERT INTO categories (parent_id, name, slug, path, sort_order)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (parent_id, name, slug, path, order),
                )
                ids[slug] = cur.lastrowid
    conn.commit()
    return ids


def classify(path: str | None, name: str | None = None) -> tuple[str | None, str | None]:
    """(category slug, the keyword that matched) for one supplier section.

    The product name is a fallback for sections that say nothing useful - "SALE",
    "Распродажа" - where the name still does.
    """
    folded = fold(path)
    if folded and not any(fold(word) == folded.strip() for word in IGNORED_SECTIONS):
        for rule in RULES:
            keyword = rule.matches(folded)
            if keyword:
                return rule.slug, keyword
    folded_name = fold(name)
    if folded_name:
        for rule in RULES:
            keyword = rule.matches(folded_name)
            if keyword:
                return rule.slug, f"название: {keyword}"
    return None, None


# ---------------------------------------------------------------------------
# Mapping suppliers in
# ---------------------------------------------------------------------------

SECTIONS_SQL = """
    SELECT sp.supplier_id, s.code AS supplier_code,
           COALESCE(sp.supplier_category_path, sp.supplier_category, '') AS section,
           COUNT(*) AS products,
           MIN(sp.name) AS sample_name
    FROM supplier_products sp
    JOIN suppliers s ON s.id = sp.supplier_id
    GROUP BY sp.supplier_id, s.code, section
"""

MAP_UPSERT = """
    INSERT INTO supplier_category_map (supplier_id, supplier_category, supplier_category_id, category_id)
    VALUES (%s, %s, NULL, %s)
    ON DUPLICATE KEY UPDATE category_id = VALUES(category_id)
"""

# A product takes the category of the supplier row it came from. Where several
# suppliers carry it, the primary one decides, the same way its price does.
ASSIGN_SQL = """
    UPDATE products p
    JOIN supplier_products sp ON sp.product_id = p.id
    JOIN supplier_category_map m
      ON m.supplier_id = sp.supplier_id
     AND m.supplier_category = COALESCE(sp.supplier_category_path, sp.supplier_category, '')
    SET p.category_id = m.category_id, p.updated_at = NOW()
    WHERE (p.primary_supplier_id = sp.supplier_id OR p.primary_supplier_id IS NULL)
      AND (p.category_id IS NULL OR p.category_id <> m.category_id)
"""


@dataclass
class MappingResult:
    sections: int = 0
    mapped: int = 0
    products_covered: int = 0
    products_unmapped: int = 0
    unmapped: list[dict[str, Any]] = None  # type: ignore[assignment]
    by_category: list[tuple[str, int]] = None  # type: ignore[assignment]


def map_sections(conn: Connection, slugs: dict[str, int]) -> MappingResult:
    """Classify every supplier section and record the mapping."""
    result = MappingResult(unmapped=[], by_category=[])
    counts: dict[str, int] = {}
    rows = fetch_all(conn, SECTIONS_SQL)

    with conn.cursor() as cur:
        for row in rows:
            result.sections += 1
            slug, _ = classify(row["section"], row["sample_name"])
            if not slug or slug not in slugs:
                result.products_unmapped += row["products"]
                result.unmapped.append(row)
                continue
            result.mapped += 1
            result.products_covered += row["products"]
            counts[slug] = counts.get(slug, 0) + row["products"]
            cur.execute(MAP_UPSERT, (row["supplier_id"], row["section"][:512], slugs[slug]))
    conn.commit()

    result.unmapped.sort(key=lambda item: -item["products"])
    result.by_category = sorted(counts.items(), key=lambda item: -item[1])
    return result


def assign_products(conn: Connection) -> int:
    with conn.cursor() as cur:
        cur.execute(ASSIGN_SQL)
        changed = cur.rowcount
    conn.commit()
    return changed


def coverage(conn: Connection) -> list[dict[str, Any]]:
    return fetch_all(
        conn,
        """
        SELECT c.name AS category, c.slug,
               parent.name AS parent,
               COUNT(p.id) AS products
        FROM categories c
        LEFT JOIN categories parent ON parent.id = c.parent_id
        LEFT JOIN products p ON p.category_id = c.id
        GROUP BY c.id, c.name, c.slug, parent.name
        ORDER BY c.sort_order
        """,
    )
