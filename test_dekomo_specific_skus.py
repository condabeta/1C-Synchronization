#!/usr/bin/env python
"""Test specific SKUs that have descriptions."""

from pathlib import Path
from staging.importers.dekomo import _normalize_row, iter_dekomo_rows

html_path = Path(r"d:\projects\1C\Декомо\content_20_08_2026_22_51.xls")

# Test specific SKUs that we know have descriptions
target_skus = ["EG_82844", "EG_86654", "EG_92206", "CL103311", "CL109321"]

found_count = 0
for i, row in enumerate(iter_dekomo_rows(html_path)):
    sku = row.get("Артикул")
    if sku in target_skus:
        normalized = _normalize_row(row, store_raw=False)
        if normalized:
            found_count += 1
            print(f"Found SKU {sku}:")
            print(f"  Description: {normalized['description'][:150] if normalized['description'] else '(empty)'}")
            print(f"  Has description in row: {bool(row.get('Дополнительное описание'))}")
            print(f"  Raw description value: {row.get('Дополнительное описание', '(empty)')[:100]}")
            print()
    
    if found_count >= len(target_skus):
        break

if found_count == 0:
    print("None of the target SKUs found in first 5000 rows")
