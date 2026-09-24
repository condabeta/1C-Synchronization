"""Supplier content we are not allowed to publish.

A supplier's feed keeps sending what the supplier sends. When a photograph turns
out to be disputed - as happened on 23.09.2026 with a Eurosvet garland, where
customers were getting pre-court claims over the picture - deleting our copy
achieves nothing: the next import restores it from the feed.

So the block lives in the database, and the import consults it. A blocked
article still imports its price and stock; only the disputed part is dropped.
Blocks are never lifted automatically.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pymysql.connections import Connection

from staging.db import fetch_all, fetch_one

IMAGES = "images"
PRODUCT = "product"

# One row per blocked article, read once per import rather than per row.
_CACHE: dict[int, dict[str, set[str]]] = {}


def reset_cache() -> None:
    _CACHE.clear()


def load_blocks(conn: Connection, supplier_id: int, *, use_cache: bool = True) -> dict[str, set[str]]:
    """{scope: {article, ...}} for one supplier. Empty if the table is absent."""
    if use_cache and supplier_id in _CACHE:
        return _CACHE[supplier_id]

    try:
        rows = fetch_all(
            conn,
            "SELECT supplier_sku, scope FROM blocked_content WHERE supplier_id = %s",
            (supplier_id,),
        )
    except Exception as exc:  # table not created yet - never block the import over it
        if "blocked_content" not in str(exc):
            raise
        rows = []

    blocks: dict[str, set[str]] = {IMAGES: set(), PRODUCT: set()}
    for row in rows:
        blocks.setdefault(row["scope"], set()).add(row["supplier_sku"])
    # A product blocked outright is also blocked for images.
    blocks[IMAGES] |= blocks[PRODUCT]
    if use_cache:
        _CACHE[supplier_id] = blocks
    return blocks


def apply_to_batch(blocks: dict[str, set[str]], batch: list[dict[str, Any]]) -> tuple[int, int]:
    """Strip blocked content from a batch before it is written.

    Returns (rows dropped, rows stripped of images). The batch is edited in
    place, so this sits in front of the upsert the way the pricing rules do.
    """
    if not blocks or not (blocks.get(IMAGES) or blocks.get(PRODUCT)):
        return 0, 0

    dropped = stripped = 0
    kept = []
    for item in batch:
        sku = item.get("supplier_sku")
        if sku in blocks.get(PRODUCT, ()):
            dropped += 1
            continue
        if sku in blocks.get(IMAGES, ()) and item.get("images_json"):
            item["images_json"] = []
            stripped += 1
        kept.append(item)

    if dropped:
        batch[:] = kept
    return dropped, stripped


@dataclass
class BlockedProduct:
    supplier_code: str
    supplier_sku: str
    scope: str
    reason: str | None


def list_blocks(conn: Connection) -> list[BlockedProduct]:
    rows = fetch_all(
        conn,
        """
        SELECT s.code, b.supplier_sku, b.scope, b.reason
        FROM blocked_content b JOIN suppliers s ON s.id = b.supplier_id
        ORDER BY s.code, b.supplier_sku
        """,
    )
    return [BlockedProduct(r["code"], r["supplier_sku"], r["scope"], r["reason"]) for r in rows]


def purge(conn: Connection) -> dict[str, int]:
    """Remove content already stored for blocked articles.

    The block stops the next import; this clears what earlier imports left
    behind, in supplier_products and in the catalogue.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE supplier_products sp
            JOIN blocked_content b
              ON b.supplier_id = sp.supplier_id AND b.supplier_sku = sp.supplier_sku
            SET sp.images_json = JSON_ARRAY()
            WHERE JSON_LENGTH(COALESCE(sp.images_json, JSON_ARRAY())) > 0
            """
        )
        supplier_rows = cur.rowcount
        cur.execute(
            """
            DELETE pi FROM product_images pi
            JOIN supplier_products sp ON sp.product_id = pi.product_id
            JOIN blocked_content b
              ON b.supplier_id = sp.supplier_id AND b.supplier_sku = sp.supplier_sku
            """
        )
        catalogue_images = cur.rowcount
        cur.execute(
            """
            UPDATE products p
            JOIN supplier_products sp ON sp.product_id = p.id
            JOIN blocked_content b
              ON b.supplier_id = sp.supplier_id AND b.supplier_sku = sp.supplier_sku
             AND b.scope = 'product'
            SET p.status = 'archived', p.is_available = 0, p.moderation_required = 0
            """
        )
        archived = cur.rowcount
    conn.commit()
    return {
        "supplier_rows_cleared": supplier_rows,
        "catalogue_images_removed": catalogue_images,
        "products_archived": archived,
    }
