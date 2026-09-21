"""URLs and GUIDs for the catalogue.

Two things every product needs before it can be published, and neither can be
made up at the last moment.

A **slug** is the product's address on the site. It has to be unique, and
supplier names are not: 5,294 groups of products share a name outright. So the
slug is derived from the name, and where that collides the internal article is
appended - deterministically, so the same product keeps the same URL between
runs. A URL that changes is a dead link and a lost search ranking.

A **GUID** is what CommerceML calls «Ид», and 1C keys everything on it. If a
product arrives with a new GUID, 1C does not recognise it as the one it already
has - it creates a second. So the GUID is generated once, stored, and never
regenerated; it is derived from the internal article, which means even a
rebuilt-from-scratch database produces the same GUIDs as before.
"""

from __future__ import annotations

import re
import uuid

from pymysql.connections import Connection

from staging.db import fetch_all, fetch_one

# The namespace ties our GUIDs to this catalogue: the same article in someone
# else's system produces a different one, and ours never move.
SVETOYAR_NAMESPACE = uuid.UUID("6f1b6b4e-3c2a-4d7e-9f10-2b3c4d5e6f70")

TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}

SLUG_MAX = 200  # the column holds 255; the rest is room for a suffix
NON_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str | None) -> str:
    """A URL-safe latin slug. Russian is transliterated, everything else drops."""
    lowered = (text or "").lower()
    transliterated = "".join(TRANSLIT.get(char, char) for char in lowered)
    slug = NON_SLUG_RE.sub("-", transliterated).strip("-")
    return slug[:SLUG_MAX].strip("-")


def unique_slug(base: str, article: str, taken: set[str]) -> str:
    """`base`, or something built from the article when that is spoken for.

    The article is what makes it unique rather than a counter: a counter depends
    on the order rows are processed, so the same product could get -2 one day
    and -5 the next, changing its URL.
    """
    candidate = base or slugify(article) or "product"
    if candidate not in taken:
        return candidate
    suffixed = f"{candidate}-{slugify(article)}"[:SLUG_MAX].strip("-")
    if suffixed and suffixed not in taken:
        return suffixed
    # Two articles that slugify identically: fall back to a stable short hash.
    digest = uuid.uuid5(SVETOYAR_NAMESPACE, article).hex[:8]
    return f"{candidate}-{digest}"[:SLUG_MAX].strip("-")


def product_guid(internal_sku: str) -> str:
    return str(uuid.uuid5(SVETOYAR_NAMESPACE, f"product:{internal_sku}"))


def category_guid(slug: str) -> str:
    return str(uuid.uuid5(SVETOYAR_NAMESPACE, f"category:{slug}"))


def meta_title(name: str | None, brand: str | None) -> str:
    """What a search engine shows. Brand plus name, trimmed to a sane length."""
    parts = [part for part in (name, brand) if part]
    title = " — ".join(dict.fromkeys(parts))
    return title[:255]


# ---------------------------------------------------------------------------
# Filling them in
# ---------------------------------------------------------------------------


def fill_categories(conn: Connection) -> int:
    rows = fetch_all(conn, "SELECT id, name, slug, onec_guid, meta_title FROM categories")
    updates = []
    for row in rows:
        guid = row["onec_guid"] or category_guid(row["slug"])
        title = row["meta_title"] or row["name"]
        if guid != row["onec_guid"] or title != row["meta_title"]:
            updates.append((guid, title, row["id"]))
    if updates:
        with conn.cursor() as cur:
            cur.executemany(
                "UPDATE categories SET onec_guid = %s, meta_title = %s WHERE id = %s", updates
            )
        conn.commit()
    return len(updates)


def fill_products(conn: Connection, *, batch: int = 5000, progress=None) -> dict[str, int]:
    """Give every product a slug, a GUID and a meta title. Existing ones stay."""
    taken = {
        row["slug"]
        for row in fetch_all(conn, "SELECT slug FROM products WHERE slug IS NOT NULL")
    }
    stats = {"slugs": 0, "guids": 0, "titles": 0}
    last_id = 0

    while True:
        rows = fetch_all(
            conn,
            """
            SELECT id, internal_sku, name, brand, slug, onec_guid, onec_code, meta_title
            FROM products
            WHERE id > %s AND status <> 'archived'
            ORDER BY id LIMIT %s
            """,
            (last_id, batch),
        )
        if not rows:
            break
        last_id = rows[-1]["id"]

        updates = []
        for row in rows:
            slug = row["slug"]
            if not slug:
                slug = unique_slug(slugify(row["name"]), row["internal_sku"], taken)
                taken.add(slug)
                stats["slugs"] += 1
            guid = row["onec_guid"] or product_guid(row["internal_sku"])
            if not row["onec_guid"]:
                stats["guids"] += 1
            title = row["meta_title"] or meta_title(row["name"], row["brand"])
            if not row["meta_title"]:
                stats["titles"] += 1
            code = row["onec_code"] or row["internal_sku"][:64]
            updates.append((slug, guid, code, title, row["id"]))

        with conn.cursor() as cur:
            cur.executemany(
                """
                UPDATE products SET slug = %s, onec_guid = %s, onec_code = %s, meta_title = %s
                WHERE id = %s
                """,
                updates,
            )
        conn.commit()
        if progress:
            progress(f"  ... {last_id:,} обработано, слагов {stats['slugs']:,}")

    return stats


def coverage(conn: Connection) -> dict:
    return fetch_one(
        conn,
        """
        SELECT COUNT(*) AS products,
               SUM(slug IS NOT NULL) AS with_slug,
               SUM(onec_guid IS NOT NULL) AS with_guid,
               SUM(meta_title IS NOT NULL) AS with_meta,
               SUM(vat_rate IS NOT NULL) AS with_vat
        FROM products WHERE status <> 'archived'
        """,
    )
