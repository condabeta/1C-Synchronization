#!/usr/bin/env python
"""Test Dekomo row keys to find description field."""

from pathlib import Path
from staging.importers.dekomo import iter_dekomo_rows

html_path = Path(r"d:\projects\1C\Декомо\content_20_08_2026_22_51.xls")

# Get first row and check all keys
for i, row in enumerate(iter_dekomo_rows(html_path, limit=1)):
    print(f"Row {i} has {len(row)} keys")
    print(f"\nAll keys in row:")
    for j, key in enumerate(row.keys()):
        print(f"  {j}: {key}")
        if "описание" in key.lower() or "description" in key.lower():
            print(f"    ^^^ FOUND DESCRIPTION KEY ^^^")
            print(f"    Value: {row[key][:100] if row[key] else '(empty)'}")
