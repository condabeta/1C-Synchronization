#!/usr/bin/env python
"""Create svetoyar_staging database and apply staging_schema.sql."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pymysql

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from staging.config import DatabaseConfig, SCHEMA_FILE
from staging.db import execute_sql_file, get_connection


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Setup Svetoyar staging database")
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Drop and recreate database before applying schema",
    )
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
        sys.stderr.reconfigure(encoding="utf-8")

    args = parse_args()
    cfg = DatabaseConfig.from_env()

    if not SCHEMA_FILE.exists():
        print(f"Schema file not found: {SCHEMA_FILE}", file=sys.stderr)
        return 1

    try:
        admin = get_connection(cfg, database=None)
    except pymysql.err.OperationalError as exc:
        print("Could not connect to MySQL.", file=sys.stderr)
        print(f"  {exc}", file=sys.stderr)
        print("\nStart MySQL service first (Windows):", file=sys.stderr)
        print("  services.msc -> MySQL96 -> Start", file=sys.stderr)
        print("  or run PowerShell as Administrator: Start-Service MySQL96", file=sys.stderr)
        print("\nThen copy config.env.example to config.env and set MYSQL_PASSWORD if needed.", file=sys.stderr)
        return 1

    try:
        with admin.cursor() as cur:
            if args.fresh:
                print(f"Dropping database `{cfg.database}`...")
                cur.execute(f"DROP DATABASE IF EXISTS `{cfg.database}`")

            print(f"Creating database `{cfg.database}` if needed...")
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{cfg.database}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        admin.commit()
        admin.close()

        conn = get_connection(cfg)
        print(f"Applying schema from {SCHEMA_FILE}...")
        count = execute_sql_file(conn, SCHEMA_FILE)

        # Ensure CSV source exists for Dekomo importer
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM suppliers WHERE code = 'dekomo'")
            supplier = cur.fetchone()
            if supplier:
                cur.execute(
                    """
                    INSERT IGNORE INTO supplier_sources (
                        supplier_id, code, source_type, format, location,
                        header_row, sku_field, schedule_cron, config_json
                    ) VALUES (
                        %s, 'price_csv', 'file', 'csv',
                        %s, 1, 'Артикул', '0 6 * * *',
                        JSON_OBJECT('delimiter', ';', 'encoding', 'utf-8-sig')
                    )
                    """,
                    (
                        supplier["id"],
                        r"D:\projects\1C\Декомо\content_12_08_2026_12_10.csv",
                    ),
                )
        conn.commit()
        conn.close()

        print(f"Done. Executed {count} SQL statements.")
        print(f"Database ready: {cfg.database}")
        return 0
    except Exception as exc:
        print(f"Setup failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
