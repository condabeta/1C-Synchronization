#!/usr/bin/env python
"""Test Dekomo description extraction."""

from pathlib import Path
from staging.importers.dekomo import _normalize_row, iter_dekomo_rows

html_path = Path(r"d:\projects\1C\Декомо\content_20_08_2026_22_51.xls")

# Test first 10 rows
for i, row in enumerate(iter_dekomo_rows(html_path, limit=10)):
    normalized = _normalize_row(row, store_raw=False)
    if normalized:
        print(f"Row {i}: SKU={normalized['supplier_sku']}")
        print(f"  Description: {normalized['description'][:100] if normalized['description'] else '(empty)'}")
        print(f"  Content hash includes description: {'description' in str(normalized['content_hash'])}")
        print()
