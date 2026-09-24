#!/usr/bin/env python
"""Apply the database migrations that have not been applied yet.

Until now the migrations in database/ were run by hand, in an order written
down in the README, with nothing recording what had already been applied. On
one developer's machine that is survivable; on the client's server, where the
system has to be stood up once and then kept current, it is not. A half-applied
migration leaves the moderation UI unable to start and no way to tell which
half ran.

    python scripts/migrate.py --status   # what is applied, what is pending
    python scripts/migrate.py --dry-run  # what would run
    python scripts/migrate.py            # apply the pending ones

Each file runs inside its own transaction and is recorded in `schema_migrations`
with a checksum of its contents. A file that changes after being applied is
reported rather than re-run: migrations are append-only, so an edited one means
the two databases have diverged.

DDL in MySQL commits implicitly, so a migration that fails halfway cannot be
rolled back by the database. It is therefore recorded as failed, with the
statement number, and the next run refuses to continue until the file or the
database is sorted out by hand.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import pymysql

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from staging.db import db_session, fetch_all, split_sql_statements

MIGRATIONS_DIR = PROJECT_ROOT / "database"

# Explicit, because order matters and a directory listing does not carry it.
# Append new migrations to the end; never reorder or edit an applied one.
MIGRATIONS = [
    "add_sync_protection.sql",
    "fix_integrity_2026_09.sql",
    "add_content_attribution.sql",
    "add_pricing_rules.sql",
    "add_salux.sql",
    "add_own_articles.sql",
    "add_certificates.sql",
    "add_field_discrepancies.sql",
    "add_publishing_fields.sql",
    "add_moderation_indexes.sql",
    "add_queue_supplier.sql",
    "add_blocked_content.sql",
]

TRACKING_TABLE = """
    CREATE TABLE IF NOT EXISTS schema_migrations (
      filename    VARCHAR(191)  NOT NULL,
      checksum    CHAR(64)      NOT NULL,
      status      ENUM('applied', 'failed') NOT NULL DEFAULT 'applied',
      statements  INT UNSIGNED  NOT NULL DEFAULT 0,
      error       TEXT          NULL,
      applied_at  DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY (filename)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply pending database migrations")
    parser.add_argument("--status", action="store_true", help="Show what is applied and pending, then exit")
    parser.add_argument("--dry-run", action="store_true", help="Say what would run, change nothing")
    parser.add_argument(
        "--mark-applied",
        metavar="FILE",
        help="Record a migration as applied without running it, for a database "
             "where it was already applied by hand",
    )
    return parser.parse_args()


def checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def applied_map(conn) -> dict[str, dict]:
    with conn.cursor() as cur:
        cur.execute(TRACKING_TABLE)
    return {row["filename"]: row for row in fetch_all(conn, "SELECT * FROM schema_migrations")}


def record(conn, filename: str, digest: str, *, status: str, statements: int, error: str | None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO schema_migrations (filename, checksum, status, statements, error)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                checksum = VALUES(checksum), status = VALUES(status),
                statements = VALUES(statements), error = VALUES(error),
                applied_at = NOW()
            """,
            (filename, digest, status, statements, error),
        )
    conn.commit()


def apply_file(conn, path: Path) -> tuple[int, str | None]:
    """Run one migration. Returns (statements run, error message or None)."""
    sql_text = path.read_text(encoding="utf-8")
    executed = 0
    with conn.cursor() as cur:
        for statement in split_sql_statements(sql_text):
            try:
                cur.execute(statement)
                executed += 1
            except Exception as exc:
                preview = " ".join(statement.split())[:200]
                return executed, f"statement #{executed + 1}: {exc} | {preview}"
    conn.commit()
    return executed, None


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
        sys.stderr.reconfigure(encoding="utf-8")

    args = parse_args()
    missing = [name for name in MIGRATIONS if not (MIGRATIONS_DIR / name).exists()]
    if missing:
        print(f"Migration files not found: {', '.join(missing)}", file=sys.stderr)
        return 1

    try:
        with db_session() as conn:
            applied = applied_map(conn)

            if args.mark_applied:
                name = args.mark_applied
                if name not in MIGRATIONS:
                    print(f"Unknown migration: {name}", file=sys.stderr)
                    return 1
                record(conn, name, checksum(MIGRATIONS_DIR / name),
                       status="applied", statements=0, error="recorded by hand, not executed")
                print(f"Recorded as applied: {name}")
                return 0

            pending, changed, failed = [], [], []
            for name in MIGRATIONS:
                digest = checksum(MIGRATIONS_DIR / name)
                row = applied.get(name)
                if row is None:
                    pending.append(name)
                elif row["status"] == "failed":
                    failed.append(name)
                elif row["checksum"] != digest:
                    changed.append(name)

            print(f"Applied: {len(applied)}  Pending: {len(pending)}")
            for name in MIGRATIONS:
                row = applied.get(name)
                state = "pending" if row is None else row["status"]
                if row is not None and row["status"] == "applied" and name in changed:
                    state = "applied, FILE CHANGED SINCE"
                print(f"  [{state:<26}] {name}")

            if failed:
                print("\nA migration is recorded as failed. Fix the database or the file by hand,",
                      file=sys.stderr)
                print("then re-run it with --mark-applied once it is sorted:", file=sys.stderr)
                for name in failed:
                    print(f"    {applied[name]['error']}", file=sys.stderr)
                return 1
            if changed:
                print("\nThese files changed after being applied. Migrations are append-only -",
                      file=sys.stderr)
                print("add a new file instead of editing an applied one.", file=sys.stderr)
                return 1
            if args.status:
                return 0
            if not pending:
                print("\nNothing to apply.")
                return 0
            if args.dry_run:
                print(f"\nWould apply {len(pending)}: {', '.join(pending)}")
                return 0

            for name in pending:
                path = MIGRATIONS_DIR / name
                print(f"\nApplying {name} ...")
                statements, error = apply_file(conn, path)
                if error:
                    record(conn, name, checksum(path), status="failed", statements=statements, error=error)
                    print(f"  FAILED after {statements} statements: {error}", file=sys.stderr)
                    return 1
                record(conn, name, checksum(path), status="applied", statements=statements, error=None)
                print(f"  ok, {statements} statements")

            print(f"\nApplied {len(pending)} migration(s).")
        return 0
    except pymysql.err.OperationalError as exc:
        print("Could not connect to MySQL.", file=sys.stderr)
        print(f"  {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
