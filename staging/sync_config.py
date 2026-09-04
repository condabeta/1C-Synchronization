"""Sync configuration utilities for field-level sync protection."""

from typing import Any
from pymysql.connections import Connection

from staging.db import fetch_all, fetch_one


def get_sync_config(conn: Connection, supplier_id: int) -> dict[str, bool]:
    """
    Get sync configuration for a supplier.
    Returns dict mapping field_name -> sync_enabled (True if sync from supplier, False if manual-only).
    """
    rows = fetch_all(
        conn,
        """
        SELECT field_name, sync_enabled
        FROM supplier_field_sync_config
        WHERE supplier_id = %s
        """,
        (supplier_id,),
    )
    return {row["field_name"]: bool(row["sync_enabled"]) for row in rows}


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
