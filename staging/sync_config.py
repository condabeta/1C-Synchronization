"""Sync configuration utilities for field-level sync protection."""

from typing import Any
from pymysql.connections import Connection

from staging.db import fetch_all, fetch_one


# One row per supplier and field, changed by hand and almost never. It was being
# re-read for every field of every product: 16 queries per supplier row, ~4.5M
# statements over the 167,000-row queue. Cached per process; call reset_cache()
# after changing the table.
_CONFIG_CACHE: dict[int, dict[str, bool]] = {}


def reset_cache() -> None:
    _CONFIG_CACHE.clear()


def get_sync_config(conn: Connection, supplier_id: int, *, use_cache: bool = True) -> dict[str, bool]:
    """
    Get sync configuration for a supplier.
    Returns dict mapping field_name -> sync_enabled (True if sync from supplier, False if manual-only).
    """
    if use_cache and supplier_id in _CONFIG_CACHE:
        return _CONFIG_CACHE[supplier_id]

    rows = fetch_all(
        conn,
        """
        SELECT field_name, sync_enabled
        FROM supplier_field_sync_config
        WHERE supplier_id = %s
        """,
        (supplier_id,),
    )
    config = {row["field_name"]: bool(row["sync_enabled"]) for row in rows}
    if use_cache:
        _CONFIG_CACHE[supplier_id] = config
    return config


def should_sync_field(conn: Connection, supplier_id: int, field_name: str, product_id: int | None = None) -> bool:
    """
    Check if a field should be synced from supplier data.
    Respects both supplier-level config and product-level override.
    """
    # Check product-level override first
    if product_id:
        override = fetch_one(
            conn,
            "SELECT sync_override_enabled FROM products WHERE id = %s",
            (product_id,),
        )
        if override and override["sync_override_enabled"]:
            # Override enabled: sync all fields
            return True
    
    # Check supplier-level config
    config = get_sync_config(conn, supplier_id)
    return config.get(field_name, True)  # Default to sync if not configured


def filter_sync_fields(conn: Connection, supplier_id: int, data: dict[str, Any], product_id: int | None = None) -> dict[str, Any]:
    """
    Filter data dict to only include fields that should be synced.
    Returns a new dict with only sync-enabled fields.
    """
    return {
        key: value
        for key, value in data.items()
        if should_sync_field(conn, supplier_id, key, product_id)
    }
