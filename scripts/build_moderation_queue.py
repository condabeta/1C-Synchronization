#!/usr/bin/env python
"""Populate moderation_queue from unmatched supplier_products."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from staging.db import db_session
from staging.moderation.service import enqueue_supplier_products


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build moderation queue from supplier imports")
    parser.add_argument("--supplier", help="Supplier code, e.g. viasvet, jazzway, crystal")
    parser.add_argument("--limit", type=int, default=100, help="Max rows to process (default 100)")
    parser.add_argument("--all", action="store_true", help="Process all unmatched rows (careful with dekomo)")
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = parse_args()
    limit = None if args.all else args.limit

    if args.all and not args.supplier:
        print("Use --supplier when passing --all (dekomo has 145k+ rows).", file=sys.stderr)
        return 1

    with db_session() as conn:
        stats = enqueue_supplier_products(
            conn,
            supplier_code=args.supplier,
            limit=limit,
        )

    print("Moderation queue build finished.")
    print(f"  Scanned:          {stats.scanned}")
    print(f"  Products created: {stats.created_products}")
    print(f"  Queued:           {stats.queued}")
    print(f"  Skipped:          {stats.skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
