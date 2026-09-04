import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

from staging.db import db_session, fetch_one

with db_session() as conn:
    run = fetch_one(
        conn,
        """
        SELECT id, status, rows_total, rows_imported, rows_updated,
               rows_skipped, rows_errors, started_at, finished_at
        FROM import_runs ORDER BY id DESC LIMIT 1
        """,
    )
    cnt = fetch_one(
        conn,
        """
        SELECT COUNT(*) AS cnt FROM supplier_products
        WHERE supplier_id = (SELECT id FROM suppliers WHERE code = 'dekomo')
        """,
    )
    running = fetch_one(
        conn, "SELECT COUNT(*) AS cnt FROM import_runs WHERE status = 'running'"
    )

print("latest_run:", run)
print("dekomo_products_in_db:", cnt["cnt"] if cnt else 0)
print("imports_still_running:", running["cnt"] if running else 0)
