"""Supplier values turned away by a protected field, kept for a manager to decide.

Names, brands and descriptions are protected from supplier imports so a manager's
edits survive the next price list. That protection used to drop whatever the
supplier sent. The client asked instead for those differences to go to
moderation with bulk accept and reject, so a supplier's corrected description is
never silently lost.

The rules:

* A difference is recorded only when both sides have a value. An empty product
  field is simply filled, as before - there is no manual edit to protect.
* One pending row per product, supplier and field. When the supplier sends yet
  another value, it replaces the pending one.
* A rejected value is remembered by its hash. The same text arriving again in the
  next price list does not ask the manager a second time.
* When the two values come to match, the pending row goes away on its own.

Like the rest of the moderation service, nothing here commits; the caller does.
"""

from __future__ import annotations

import hashlib
from typing import Any

from pymysql.connections import Connection

from staging.db import fetch_all, fetch_one
from staging.pricing import as_decimal

# field name -> products column. Also the whitelist: field names end up in SQL
# as column names, so nothing outside this map is ever accepted.
PRODUCT_FIELDS = {
    "name": "name",
    "brand": "brand",
    "description": "description",
    "price": "price",
    "price_old": "price_old",
    "price_retail": "price_retail",
    "stock_qty": "stock_qty",
    "is_available": "is_available",
}

STATUSES = ("pending", "accepted", "rejected")


def normalize(value: Any) -> str:
    """The form two values are compared in.

    Numbers compare as numbers, so 950.0000 from the database and 950 from a
    price list are the same value rather than a difference to review. Text
    compares with whitespace collapsed, so a re-saved file with different line
    breaks is not a difference either.
    """
    if value is None:
        return ""
    number = as_decimal(value)
    if number is not None:
        return format(number.normalize(), "f")
    return " ".join(str(value).split())


def _hash(value: Any) -> str:
    return hashlib.sha256(normalize(value).encode("utf-8")).hexdigest()


def record(
    conn: Connection,
    product_id: int,
    supplier_id: int,
    field_name: str,
    current_value: Any,
    supplier_value: Any,
) -> str:
    """Note what a protected field turned away. Returns what happened.

    "empty"     - one side has no value, so there is nothing to choose between
    "equal"     - no real difference; any pending row for the field is cleared
    "rejected"  - the manager already rejected exactly this value
    "accepted"  - this value was already accepted
    "pending"   - a pending row now holds this value
    """
    if field_name not in PRODUCT_FIELDS:
        raise ValueError(f"Unknown product field: {field_name}")

    # An empty product field is filled by the import directly, and an empty
    # supplier value must never be offered - accepting it would blank the field,
    # and `name` cannot be blank at all.
    if not normalize(current_value) or not normalize(supplier_value):
        return "empty"

    if normalize(current_value) == normalize(supplier_value):
        _clear_pending(conn, product_id, supplier_id, field_name)
        return "equal"

    value_hash = _hash(supplier_value)
    existing = fetch_one(
        conn,
        """
        SELECT id, status FROM field_discrepancies
        WHERE product_id = %s AND supplier_id = %s AND field_name = %s AND value_hash = %s
        """,
        (product_id, supplier_id, field_name, value_hash),
    )
    if existing and existing["status"] != "pending":
        return existing["status"]

    with conn.cursor() as cur:
        # A newer supplier value supersedes whatever was still waiting.
        cur.execute(
            """
            DELETE FROM field_discrepancies
            WHERE product_id = %s AND supplier_id = %s AND field_name = %s
              AND status = 'pending' AND value_hash <> %s
            """,
            (product_id, supplier_id, field_name, value_hash),
        )
        if existing:
            cur.execute(
                "UPDATE field_discrepancies SET current_value = %s WHERE id = %s",
                (_as_text(current_value), existing["id"]),
            )
        else:
            cur.execute(
                """
                INSERT INTO field_discrepancies (
                    product_id, supplier_id, field_name, current_value, supplier_value, value_hash
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    product_id,
                    supplier_id,
                    field_name,
                    _as_text(current_value),
                    _as_text(supplier_value),
                    value_hash,
                ),
            )
    return "pending"


def _as_text(value: Any) -> str | None:
    return None if value is None else str(value)


def _clear_pending(conn: Connection, product_id: int, supplier_id: int, field_name: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM field_discrepancies
            WHERE product_id = %s AND supplier_id = %s AND field_name = %s AND status = 'pending'
            """,
            (product_id, supplier_id, field_name),
        )


def _filters(
    *,
    status: str | None = None,
    ids: list[int] | None = None,
    supplier_code: str | None = None,
    field_name: str | None = None,
    search: str | None = None,
) -> tuple[str, list[Any]]:
    """WHERE clause over field_discrepancies d, products p, suppliers s."""
    where, params = [], []
    if status:
        where.append("d.status = %s")
        params.append(status)
    if ids is not None:
        if not ids:
            where.append("1 = 0")
        else:
            where.append(f"d.id IN ({', '.join(['%s'] * len(ids))})")
            params.extend(ids)
    if supplier_code:
        where.append("s.code = %s")
        params.append(supplier_code)
    if field_name:
        where.append("d.field_name = %s")
        params.append(field_name)
    if search:
        where.append("(p.internal_sku LIKE %s OR p.name LIKE %s)")
        params.extend([f"%{search}%", f"%{search}%"])
    return (" AND ".join(where) or "1 = 1"), params


FROM_SQL = """
    FROM field_discrepancies d
    JOIN products p ON p.id = d.product_id
    JOIN suppliers s ON s.id = d.supplier_id
"""


def list_discrepancies(
    conn: Connection,
    *,
    status: str = "pending",
    supplier_code: str | None = None,
    field_name: str | None = None,
    search: str | None = None,
    page: int = 1,
    per_page: int = 50,
) -> tuple[list[dict[str, Any]], int]:
    where, params = _filters(
        status=status, supplier_code=supplier_code, field_name=field_name, search=search
    )
    total = fetch_one(conn, f"SELECT COUNT(*) AS cnt {FROM_SQL} WHERE {where}", params)
    rows = fetch_all(
        conn,
        f"""
        SELECT d.id, d.product_id, d.field_name, d.current_value, d.supplier_value, d.status,
               d.detected_at, d.resolved_at, d.resolved_by,
               p.internal_sku, p.name AS product_name,
               s.code AS supplier_code, s.name AS supplier_name
        {FROM_SQL}
        WHERE {where}
        ORDER BY d.detected_at DESC, d.id DESC
        LIMIT %s OFFSET %s
        """,
        [*params, per_page, (page - 1) * per_page],
    )
    return rows, int(total["cnt"]) if total else 0


def pending_summary(conn: Connection) -> list[dict[str, Any]]:
    """Pending differences by supplier and field, for the dashboard."""
    return fetch_all(
        conn,
        """
        SELECT s.code AS supplier_code, s.name AS supplier_name, d.field_name, COUNT(*) AS cnt
        FROM field_discrepancies d JOIN suppliers s ON s.id = d.supplier_id
        WHERE d.status = 'pending'
        GROUP BY s.code, s.name, d.field_name
        ORDER BY cnt DESC
        """,
    )


def resolve(
    conn: Connection,
    *,
    action: str,
    reviewer: str,
    ids: list[int] | None = None,
    supplier_code: str | None = None,
    field_name: str | None = None,
    search: str | None = None,
) -> int:
    """Accept or reject pending differences. Returns how many were resolved.

    Pass ids to resolve a selection, or leave ids out to resolve everything
    pending that matches the filters. Accepting writes the supplier's value into
    the product; rejecting leaves the product as it is.

    If two suppliers of the same product both have a pending value for the same
    field, accepting both applies them in an unspecified order - resolve such a
    product one supplier at a time.
    """
    if action not in ("accept", "reject"):
        raise ValueError(f"Unknown action: {action}")
    if field_name and field_name not in PRODUCT_FIELDS:
        raise ValueError(f"Unknown product field: {field_name}")

    where, params = _filters(
        status="pending", ids=ids, supplier_code=supplier_code, field_name=field_name, search=search
    )

    with conn.cursor() as cur:
        if action == "accept":
            # One statement per field: the column name cannot be a parameter,
            # and it comes from the whitelist, never from the request.
            for field, column in PRODUCT_FIELDS.items():
                if field_name and field != field_name:
                    continue
                cur.execute(
                    f"""
                    UPDATE products p
                    JOIN field_discrepancies d ON d.product_id = p.id
                    JOIN suppliers s ON s.id = d.supplier_id
                    SET p.{column} = d.supplier_value, p.updated_at = NOW()
                    WHERE d.field_name = %s AND {where}
                    """,
                    [field, *params],
                )
        cur.execute(
            f"""
            UPDATE field_discrepancies d
            JOIN products p ON p.id = d.product_id
            JOIN suppliers s ON s.id = d.supplier_id
            SET d.status = %s, d.resolved_at = NOW(), d.resolved_by = %s
            WHERE {where}
            """,
            ["accepted" if action == "accept" else "rejected", reviewer[:128], *params],
        )
        return cur.rowcount
