from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pymysql.connections import Connection

from staging.db import fetch_all, fetch_one
from staging.moderation.discrepancies import record as record_discrepancy
from staging.sync_config import should_sync_field


@dataclass
class EnqueueStats:
    scanned: int = 0
    created_products: int = 0
    queued: int = 0
    skipped: int = 0
    updated_links: int = 0


INTERNAL_SKU_MAX = 128  # products.internal_sku is VARCHAR(128), and unique


def _internal_sku(supplier_code: str, supplier_sku: str) -> str:
    """A stable key for the catalogue row a supplier article created.

    Supplier articles are themselves up to 128 characters, so the prefixed key
    can overflow. Cutting it would let two articles that share their first
    characters collapse onto one catalogue row - a unique-key violation that
    rolls back the whole batch - so an over-long key keeps its head and ends
    with a hash of the full article instead.
    """
    sku = f"{supplier_code}:{supplier_sku}"
    if len(sku) <= INTERNAL_SKU_MAX:
        return sku
    digest = hashlib.sha1(sku.encode("utf-8")).hexdigest()[:12]
    return f"{sku[: INTERNAL_SKU_MAX - len(digest) - 1]}~{digest}"


def _parse_images(raw: Any) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(item) for item in raw if item]
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return [str(item) for item in data if item]
        except json.JSONDecodeError:
            return [raw]
    return []


def _parse_attrs(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _detect_queue_reason(row: dict[str, Any], *, is_new: bool) -> str:
    images = _parse_images(row.get("images_json"))
    description = (row.get("description") or "").strip()
    attrs = _parse_attrs(row.get("attributes_json"))
    if not description:
        description = (attrs.get("description") or "").strip()

    if is_new:
        if not images:
            return "missing_images"
        if not description:
            return "missing_description"
        return "new_product"
    if not images:
        return "missing_images"
    return "major_change"


def _priority_for_reason(reason: str, supplier_code: str) -> int:
    if supplier_code in {"viasvet", "jazzway", "crystal"}:
        return 2
    if reason in {"missing_images", "price_anomaly"}:
        return 3
    return 5


def _has_pending_queue(conn: Connection, product_id: int) -> bool:
    row = fetch_one(
        conn,
        """
        SELECT id FROM moderation_queue
        WHERE product_id = %s AND status IN ('pending', 'in_review')
        LIMIT 1
        """,
        (product_id,),
    )
    return row is not None


def _link_supplier_product(conn: Connection, product_id: int, row: dict[str, Any]) -> None:
    """Point a supplier row at a catalogue row, and carry its offer and images.

    Shared by the two ways a supplier row reaches the catalogue: a product
    created for it, or an existing one adopted.
    """
    with conn.cursor() as cur:

        cur.execute(
            """
            UPDATE supplier_products
            SET product_id = %s,
                match_status = 'matched',
                is_new = 0
            WHERE id = %s
            """,
            (product_id, row["id"]),
        )

        cur.execute(
            """
            INSERT INTO product_supplier_links (
                product_id, supplier_id, supplier_product_id, supplier_sku, link_type
            ) VALUES (%s, %s, %s, %s, 'primary')
            ON DUPLICATE KEY UPDATE
                supplier_product_id = VALUES(supplier_product_id),
                is_active = 1
            """,
            (product_id, row["supplier_id"], row["id"], row["supplier_sku"]),
        )

        cur.execute(
            """
            INSERT INTO product_supplier_offers (
                product_id, supplier_id, supplier_product_id, supplier_sku,
                price, price_retail, stock_qty, is_available
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                supplier_product_id = VALUES(supplier_product_id),
                price = VALUES(price),
                price_retail = VALUES(price_retail),
                stock_qty = VALUES(stock_qty),
                is_available = VALUES(is_available),
                imported_at = NOW()
            """,
            (
                product_id,
                row["supplier_id"],
                row["id"],
                row["supplier_sku"],
                row.get("price"),
                row.get("price_retail"),
                row.get("stock_qty"),
                row.get("is_available"),
            ),
        )


    _sync_product_images(conn, product_id, row["supplier_id"], _parse_images(row.get("images_json")))


def _create_product_from_supplier(conn: Connection, row: dict[str, Any]) -> int:
    attrs = _parse_attrs(row.get("attributes_json"))
    description = row.get("description") or attrs.get("description")
    internal = _internal_sku(row["supplier_code"], row["supplier_sku"])

    # A catalogue row for this article may already exist while the supplier row
    # points at nothing: that is what the 683 "ghost" products are - their link
    # was deleted and the supplier row came back later as unmatched. Inserting
    # again raises a duplicate key and rolls back the whole run, so the existing
    # row is adopted instead.
    existing = fetch_one(conn, "SELECT id FROM products WHERE internal_sku = %s", (internal,))
    if existing:
        product_id = int(existing["id"])
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE products SET primary_supplier_id = COALESCE(primary_supplier_id, %s),
                                    updated_at = NOW()
                WHERE id = %s
                """,
                (row["supplier_id"], product_id),
            )
        _link_supplier_product(conn, product_id, row)
        return product_id

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO products (
                internal_sku, name, brand, description, status, moderation_required,
                primary_supplier_id, price, price_old, price_retail, stock_qty,
                is_available, barcode, category_id, content_hash
            ) VALUES (
                %s, %s, %s, %s, 'pending_moderation', 1,
                %s, %s, %s, %s, %s,
                COALESCE(%s, 0), %s,
                -- the tree the supplier's section maps to, if it maps to one
                (SELECT category_id FROM supplier_category_map
                  WHERE supplier_id = %s
                    AND supplier_category = COALESCE(%s, %s, '') LIMIT 1),
                %s
            )
            """,
            (
                internal,
                row.get("name") or row["supplier_sku"],
                row.get("brand"),
                description,
                row["supplier_id"],
                row.get("price"),
                row.get("price_old"),
                row.get("price_retail"),
                row.get("stock_qty"),
                row.get("is_available"),
                # Neither was carried over before: the catalogue had no barcode
                # at all, and no category until one was assigned in a later pass.
                row.get("barcode"),
                row["supplier_id"],
                row.get("supplier_category_path"),
                row.get("supplier_category"),
                row.get("content_hash"),
            ),
        )
        product_id = int(cur.lastrowid)

    _link_supplier_product(conn, product_id, row)
    _record_status(conn, product_id, None, "pending_moderation", "system", "Created from supplier import")
    return product_id


_JUNK_IMAGE = {"nan", "none", "null", "-", "0", "false"}


def _usable_image(value: Any) -> bool:
    text = (value or "").strip()
    return bool(text) and text.lower() not in _JUNK_IMAGE


def _sync_product_images(
    conn: Connection,
    product_id: int,
    supplier_id: int,
    images: list[str],
) -> int:
    """Add images this supplier sends that the product does not have yet.

    Existing rows are left alone, so a manually curated gallery keeps its order
    and any image added by hand survives.
    """
    # "nan" is a pandas empty cell that reached the feed as text. 121 products
    # had it as their *main* image, so their page led with a broken picture.
    images = [image for image in images if _usable_image(image)]
    if not images:
        return 0

    known = {
        row["source_path"]
        for row in fetch_all(
            conn,
            "SELECT source_path FROM product_images WHERE product_id = %s",
            (product_id,),
        )
    }
    next_sort = len(known)
    added = 0

    with conn.cursor() as cur:
        for image in images:
            path = image[:1024]
            if path in known:
                continue
            source_type = "local_file" if re.match(r"^[A-Za-z]:\\", image) else "url"
            cur.execute(
                """
                INSERT INTO product_images (
                    product_id, supplier_id, image_type, source_type, source_path, sort_order
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    product_id,
                    supplier_id,
                    "main" if not known else "gallery",
                    source_type,
                    path,
                    next_sort,
                ),
            )
            known.add(path)
            next_sort += 1
            added += 1
    return added


def _update_product_from_supplier(
    conn: Connection,
    product_id: int,
    supplier_id: int,
    supplier_data: dict[str, Any],
) -> None:
    """Update product from supplier data respecting sync protection.

    A protected field is protected against being *overwritten*, not against being
    filled in. If the product has no value yet there is no manual edit to lose,
    so supplier content still lands - otherwise a supplier who starts sending
    descriptions could never fill the ones already in the catalogue.
    """
    # Build update SQL with only sync-enabled fields
    update_fields = []
    update_values = []

    # Define field mappings from supplier_data to products table
    field_mappings = {
        "name": "name",
        "brand": "brand",
        "description": "description",
        "price": "price",
        "price_old": "price_old",
        "price_retail": "price_retail",
        "stock_qty": "stock_qty",
        "is_available": "is_available",
    }

    columns = sorted(set(field_mappings.values()))
    current = fetch_one(
        conn,
        f"SELECT {', '.join(columns)}, sync_override_enabled FROM products WHERE id = %s",
        (product_id,),
    ) or {}
    # Read once, with the row we are already fetching, instead of once per
    # field: this ran nine extra SELECTs for every supplier row processed.
    override = bool(current.get("sync_override_enabled"))

    def _empty(value: Any) -> bool:
        return value is None or (isinstance(value, str) and not value.strip())

    for supplier_field, product_field in field_mappings.items():
        if supplier_field not in supplier_data:
            continue
        value = supplier_data[supplier_field]
        allowed = override or should_sync_field(conn, supplier_id, supplier_field)
        if not allowed:
            # Fill-if-empty: nothing to protect when the product field is blank.
            allowed = _empty(current.get(product_field)) and not _empty(value)
            if not allowed:
                # Protected and already filled: ours stays, but what the supplier
                # sent goes to moderation instead of being dropped.
                record_discrepancy(
                    conn, product_id, supplier_id, supplier_field,
                    current.get(product_field), value,
                )
        if allowed:
            update_fields.append(f"{product_field} = %s")
            update_values.append(value)

    _sync_product_images(
        conn, product_id, supplier_id, _parse_images(supplier_data.get("images_json"))
    )

    if not update_fields:
        return  # No fields to update
    
    update_values.append(product_id)
    
    with conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE products
            SET {', '.join(update_fields)}, updated_at = NOW()
            WHERE id = %s
            """,
            update_values,
        )


def _record_status(
    conn: Connection,
    product_id: int,
    old_status: str | None,
    new_status: str,
    changed_by: str | None,
    reason: str | None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO product_status_history (
                product_id, old_status, new_status, changed_by, reason
            ) VALUES (%s, %s, %s, %s, %s)
            """,
            (product_id, old_status, new_status, changed_by, reason),
        )


def _enqueue_product(
    conn: Connection,
    *,
    product_id: int,
    supplier_product_id: int | None,
    import_run_id: int | None,
    queue_reason: str,
    priority: int,
    diff: dict[str, Any] | None = None,
) -> bool:
    if _has_pending_queue(conn, product_id):
        return False

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO moderation_queue (
                product_id, supplier_product_id, supplier_id, import_run_id,
                queue_reason, priority, status, diff_json
            ) VALUES (
                %s, %s,
                (SELECT supplier_id FROM supplier_products WHERE id = %s),
                %s, %s, %s, 'pending', %s
            )
            """,
            (
                product_id,
                supplier_product_id,
                supplier_product_id,
                import_run_id,
                queue_reason,
                priority,
                json.dumps(diff, ensure_ascii=False) if diff else None,
            ),
        )
        cur.execute(
            """
            UPDATE products
            SET status = 'pending_moderation', moderation_required = 1
            WHERE id = %s AND status NOT IN ('published', 'synced_1c')
            """,
            (product_id,),
        )
    return True


def enqueue_supplier_products(
    conn: Connection,
    *,
    supplier_code: str | None = None,
    limit: int | None = 100,
    include_changed: bool = True,
) -> EnqueueStats:
    stats = EnqueueStats()
    params: list[Any] = []
    # Rows already matched to a product still need picking up when the supplier
    # changed them - otherwise new descriptions and images never reach the
    # catalogue, because matching happens once and never again.
    if include_changed:
        where = ["(sp.match_status = 'unmatched' OR sp.is_changed = 1)"]
    else:
        where = ["sp.match_status = 'unmatched'", "sp.is_new = 1"]
    if supplier_code:
        where.append("s.code = %s")
        params.append(supplier_code)

    limit_sql = f"LIMIT {int(limit)}" if limit else ""

    rows = fetch_all(
        conn,
        f"""
        SELECT
            sp.id, sp.supplier_id, sp.import_run_id, sp.supplier_sku, sp.name, sp.brand,
            sp.description, sp.price, sp.price_old, sp.price_retail, sp.stock_qty,
            sp.is_available, sp.images_json, sp.attributes_json, sp.content_hash,
            sp.barcode, sp.supplier_category, sp.supplier_category_path,
            sp.product_id, sp.is_new, sp.is_changed,
            s.code AS supplier_code
        FROM supplier_products sp
        JOIN suppliers s ON s.id = sp.supplier_id
        WHERE {' AND '.join(where)}
        ORDER BY sp.is_new DESC, sp.last_seen_at DESC
        {limit_sql}
        """,
        params,
    )

    for row in rows:
        stats.scanned += 1
        product_id = row.get("product_id")

        if product_id and row.get("is_changed") and include_changed:
            # Update product from supplier data respecting sync protection
            supplier_data = {
                "name": row.get("name"),
                "brand": row.get("brand"),
                "description": row.get("description"),
                "price": row.get("price"),
                "price_old": row.get("price_old"),
                "price_retail": row.get("price_retail"),
                "stock_qty": row.get("stock_qty"),
                "is_available": row.get("is_available"),
                "images_json": row.get("images_json"),
            }
            _update_product_from_supplier(conn, product_id, row["supplier_id"], supplier_data)
            
            reason = _detect_queue_reason(row, is_new=False)
            priority = _priority_for_reason(reason, row["supplier_code"])
            diff = {
                "supplier_sku": row["supplier_sku"],
                "content_hash": row.get("content_hash"),
            }
            queued = _enqueue_product(
                conn,
                product_id=product_id,
                supplier_product_id=row["id"],
                import_run_id=row.get("import_run_id"),
                queue_reason=reason,
                priority=priority,
                diff=diff,
            )
            stats.queued += 1 if queued else 0
            stats.skipped += 0 if queued else 1
            # Cleared either way. The supplier's change has already been applied
            # to the product above, and when a queue item for it is still
            # pending the change is folded into that item - there is nothing
            # left to pick up. Leaving the flag set made every later run
            # re-select the same rows, so the queue never drained: 151,920 of
            # 151,921 changed rows were stuck in exactly that state.
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE supplier_products SET is_changed = 0 WHERE id = %s",
                    (row["id"],),
                )
            continue

        if product_id:
            stats.skipped += 1
            continue

        product_id = _create_product_from_supplier(conn, row)
        stats.created_products += 1

        reason = _detect_queue_reason(row, is_new=True)
        priority = _priority_for_reason(reason, row["supplier_code"])
        if _enqueue_product(
            conn,
            product_id=product_id,
            supplier_product_id=row["id"],
            import_run_id=row.get("import_run_id"),
            queue_reason=reason,
            priority=priority,
            diff={"supplier_sku": row["supplier_sku"], "supplier": row["supplier_code"]},
        ):
            stats.queued += 1
        else:
            stats.skipped += 1

    return stats


# SUM(status = '...') has to read every row. GROUP BY status reads the status
# index instead, which is the difference between a four-second dashboard and an
# instant one at 167,000 queue rows.
QUEUE_STATUS_KEYS = {"pending": "pending", "in_review": "in_review",
                     "approved": "approved", "rejected": "rejected"}
PRODUCT_STATUS_KEYS = {"pending_moderation": "pending_products",
                       "approved": "approved_products", "published": "published_products"}


def _counts_by_status(conn: Connection, table: str, keys: dict[str, str]) -> dict[str, int]:
    rows = fetch_all(conn, f"SELECT status, COUNT(*) AS cnt FROM {table} GROUP BY status")
    counts = {key: 0 for key in keys.values()}
    for row in rows:
        key = keys.get(row["status"])
        if key:
            counts[key] = row["cnt"]
    return counts


def get_dashboard_stats(conn: Connection) -> dict[str, Any]:
    summary = _counts_by_status(conn, "moderation_queue", QUEUE_STATUS_KEYS)
    products = _counts_by_status(conn, "products", PRODUCT_STATUS_KEYS)
    by_supplier = fetch_all(
        conn,
        """
        SELECT s.name AS supplier_name, s.code AS supplier_code, COUNT(*) AS cnt
        FROM moderation_queue mq
        JOIN suppliers s ON s.id = mq.supplier_id
        WHERE mq.status = 'pending'
        GROUP BY s.id, s.name, s.code
        ORDER BY cnt DESC
        """,
    )
    unmatched = fetch_one(
        conn,
        "SELECT COUNT(*) AS cnt FROM supplier_products WHERE match_status = 'unmatched'",
    )
    unmatched_by_supplier = fetch_all(
        conn,
        """
        SELECT s.code AS supplier_code, COUNT(*) AS cnt
        FROM supplier_products sp
        JOIN suppliers s ON s.id = sp.supplier_id
        WHERE sp.match_status = 'unmatched'
        GROUP BY s.id, s.code
        ORDER BY s.code
        """,
    )
    # Convert to dict for easy lookup
    unmatched_dict = {row["supplier_code"]: row["cnt"] for row in unmatched_by_supplier}
    return {
        "queue": summary,
        "products": products,
        "pending_by_supplier": by_supplier,
        "unmatched_supplier_products": unmatched["cnt"] if unmatched else 0,
        "unmatched_by_supplier": unmatched_dict,
    }


def list_queue_items(
    conn: Connection,
    *,
    status: str = "pending",
    supplier_code: str | None = None,
    page: int = 1,
    per_page: int = 20,
    search: str | None = None,
    queue_reason: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    params: list[Any] = [status]
    where = ["mq.status = %s"]
    if supplier_code:
        where.append("mq.supplier_id = (SELECT id FROM suppliers WHERE code = %s)")
        params.append(supplier_code)
    if queue_reason:
        where.append("mq.queue_reason = %s")
        params.append(queue_reason)
    if search:
        where.append("(p.name LIKE %s OR p.internal_sku LIKE %s OR sp.supplier_sku LIKE %s OR s.name LIKE %s)")
        search_pattern = f"%{search}%"
        params.extend([search_pattern, search_pattern, search_pattern, search_pattern])

    # Unfiltered, the count is answerable from the queue's own index; the joins
    # exist only to support the filters, and dragging 219,000 rows through them
    # to count what the index already knows cost seconds per page view.
    if len(where) == 1:
        total_row = fetch_one(
            conn, "SELECT COUNT(*) AS cnt FROM moderation_queue mq WHERE mq.status = %s", params
        )
    else:
        total_row = fetch_one(
            conn,
            f"""
            SELECT COUNT(*) AS cnt
            FROM moderation_queue mq
            LEFT JOIN supplier_products sp ON sp.id = mq.supplier_product_id
            LEFT JOIN suppliers s ON s.id = sp.supplier_id
            LEFT JOIN products p ON p.id = mq.product_id
            WHERE {' AND '.join(where)}
            """,
            params,
        )
    total = int(total_row["cnt"]) if total_row else 0
    # Past the last page there is nothing to fetch, and asking MySQL for a deep
    # OFFSET makes it walk the whole index to return an empty result: page
    # 99999999 took eight seconds to say "no rows".
    pages = max((total + per_page - 1) // per_page, 1)
    page = min(page, pages)
    offset = (page - 1) * per_page

    rows = fetch_all(
        conn,
        f"""
        SELECT
            mq.id, mq.queue_reason, mq.priority, mq.status, mq.created_at,
            mq.review_notes, mq.reviewer, mq.reviewed_at,
            p.id AS product_id, p.internal_sku, p.name AS product_name,
            p.price, p.price_retail, p.status AS product_status,
            sp.supplier_sku, sp.supplier_category,
            s.code AS supplier_code, s.name AS supplier_name
        FROM moderation_queue mq
        JOIN products p ON p.id = mq.product_id
        LEFT JOIN supplier_products sp ON sp.id = mq.supplier_product_id
        LEFT JOIN suppliers s ON s.id = sp.supplier_id
        WHERE {' AND '.join(where)}
        ORDER BY mq.priority ASC, mq.created_at ASC
        LIMIT %s OFFSET %s
        """,
        [*params, per_page, offset],
    )
    return rows, total


def get_queue_item(conn: Connection, queue_id: int) -> dict[str, Any] | None:
    item = fetch_one(
        conn,
        """
        SELECT
            mq.*,
            p.internal_sku, p.name AS product_name, p.brand, p.description,
            p.price, p.price_retail, p.price_old, p.stock_qty, p.status AS product_status,
            sp.supplier_sku, sp.supplier_category, sp.product_url, sp.attributes_json,
            sp.images_json, sp.price AS supplier_price,
            s.code AS supplier_code, s.name AS supplier_name
        FROM moderation_queue mq
        JOIN products p ON p.id = mq.product_id
        LEFT JOIN supplier_products sp ON sp.id = mq.supplier_product_id
        LEFT JOIN suppliers s ON s.id = sp.supplier_id
        WHERE mq.id = %s
        """,
        (queue_id,),
    )
    if not item:
        return None

    images = fetch_all(
        conn,
        """
        SELECT image_type, source_type, source_path, sort_order
        FROM product_images
        WHERE product_id = %s AND is_active = 1
        ORDER BY sort_order
        """,
        (item["product_id"],),
    )
    item["product_images"] = images
    item["parsed_images_json"] = _parse_images(item.get("images_json"))
    item["parsed_attributes"] = _parse_attrs(item.get("attributes_json"))
    if item.get("diff_json") and isinstance(item["diff_json"], str):
        try:
            item["diff_json"] = json.loads(item["diff_json"])
        except json.JSONDecodeError:
            pass
    return item


def approve_queue_item(
    conn: Connection,
    queue_id: int,
    *,
    reviewer: str = "moderator",
    notes: str | None = None,
) -> bool:
    item = fetch_one(
        conn,
        "SELECT id, product_id, status FROM moderation_queue WHERE id = %s",
        (queue_id,),
    )
    if not item or item["status"] not in {"pending", "in_review"}:
        return False

    product = fetch_one(conn, "SELECT id, status FROM products WHERE id = %s", (item["product_id"],))
    old_status = product["status"] if product else None

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE moderation_queue
            SET status = 'approved',
                reviewer = %s,
                review_notes = %s,
                reviewed_at = %s
            WHERE id = %s
            """,
            (reviewer, notes, datetime.now(), queue_id),
        )
        # A product that is already live stays live: approving a change to it
        # must not drop it back to 'approved', which would read as "not
        # published yet" to everything downstream.
        cur.execute(
            """
            UPDATE products
            SET status = IF(status IN ('published', 'synced_1c'), status, 'approved'),
                moderation_required = 0,
                sync_1c_status = 'pending'
            WHERE id = %s
            """,
            (item["product_id"],),
        )
        # One outstanding export per product. Re-approving after a rejection, or
        # approving two queue items for the same product, used to leave several
        # identical rows for a consumer to send twice.
        cur.execute(
            """
            INSERT INTO sync_outbox (entity_type, entity_id, target_system, action, payload_json)
            SELECT 'product', %s, 'onec', 'export', JSON_OBJECT('product_id', %s)
            FROM DUAL WHERE NOT EXISTS (
                SELECT 1 FROM (SELECT id FROM sync_outbox
                    WHERE entity_type = 'product' AND entity_id = %s
                      AND target_system = 'onec' AND status = 'pending') AS pending
            )
            """,
            (item["product_id"], item["product_id"], item["product_id"]),
        )

    _record_status(conn, item["product_id"], old_status, "approved", reviewer, notes or "Approved in moderation")
    return True


def reject_queue_item(
    conn: Connection,
    queue_id: int,
    *,
    reviewer: str = "moderator",
    notes: str | None = None,
) -> bool:
    item = fetch_one(
        conn,
        "SELECT id, product_id, status FROM moderation_queue WHERE id = %s",
        (queue_id,),
    )
    if not item or item["status"] not in {"pending", "in_review"}:
        return False

    product = fetch_one(conn, "SELECT id, status FROM products WHERE id = %s", (item["product_id"],))
    old_status = product["status"] if product else None

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE moderation_queue
            SET status = 'rejected',
                reviewer = %s,
                review_notes = %s,
                reviewed_at = %s
            WHERE id = %s
            """,
            (reviewer, notes, datetime.now(), queue_id),
        )
        # Rejecting a change to a published product means "do not apply this
        # change", not "take the product off the site". Removing a live product
        # is a separate, deliberate act - it needs a delete sent to the site,
        # which nothing does yet.
        cur.execute(
            """
            UPDATE products
            SET status = IF(status IN ('published', 'synced_1c'), status, 'rejected'),
                moderation_required = 0,
                sync_1c_status = IF(status IN ('published', 'synced_1c'), sync_1c_status, NULL)
            WHERE id = %s
            """,
            (item["product_id"],),
        )
        # An approval this rejection reverses must not stay queued for export.
        cur.execute(
            """
            DELETE FROM sync_outbox
            WHERE entity_type = 'product' AND entity_id = %s AND status = 'pending'
              AND EXISTS (SELECT 1 FROM products
                          WHERE id = %s AND status NOT IN ('published', 'synced_1c'))
            """,
            (item["product_id"], item["product_id"]),
        )

    _record_status(conn, item["product_id"], old_status, "rejected", reviewer, notes or "Rejected in moderation")
    return True
