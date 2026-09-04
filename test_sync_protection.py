#!/usr/bin/env python
"""Test sync protection configuration."""

from staging.db import db_session, fetch_all

with db_session() as conn:
    # Check sync configuration
    configs = fetch_all(conn, """
        SELECT s.code as supplier_code, sfc.field_name, sfc.sync_enabled
        FROM supplier_field_sync_config sfc
        JOIN suppliers s ON s.id = sfc.supplier_id
        ORDER BY s.code, sfc.field_name
    """)
    
    print("Sync Configuration:")
    for config in configs:
        status = "SYNC" if config['sync_enabled'] else "MANUAL"
        print(f"  {config['supplier_code']}.{config['field_name']}: {status}")
    
    # Check if products table has sync_override_enabled column
    try:
        result = fetch_all(conn, "SHOW COLUMNS FROM products LIKE 'sync_override_enabled'")
        if result:
            print(f"\n✓ sync_override_enabled column exists in products table")
        else:
            print(f"\n✗ sync_override_enabled column missing from products table")
    except Exception as e:
        print(f"\n✗ Error checking products table: {e}")
