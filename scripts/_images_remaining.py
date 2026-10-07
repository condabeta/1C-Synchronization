#!/usr/bin/env python
"""Exit 1 while a supplier still has images to download, 0 when none remain.

Used by run_dekomo_images.cmd to decide whether to loop again. Takes the supplier
code as its one argument.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from staging.db import db_session, fetch_all

supplier = sys.argv[1] if len(sys.argv) > 1 else "dekomo"
with db_session() as conn:
    remaining = fetch_all(
        conn,
        """
        SELECT COUNT(DISTINCT source_path) AS n
        FROM product_images
        WHERE supplier_id = (SELECT id FROM suppliers WHERE code = %s)
          AND (stored_path IS NULL OR stored_path = '')
        """,
        (supplier,),
    )[0]["n"]

print(f"{supplier}: {remaining} image URLs remaining")
sys.exit(1 if remaining > 0 else 0)
