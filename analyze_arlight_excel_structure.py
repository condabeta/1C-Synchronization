#!/usr/bin/env python
"""Analyze Arlight Excel structure to find SKU column."""

import pandas as pd

excel_path = r"D:\projects\1C\Арлайт\Прайс от 18.08.2026.xlsx"

# Check first 10 rows to find header
for header_row in range(10):
    try:
        df = pd.read_excel(excel_path, header=header_row)
        print(f"\nHeader row {header_row}:")
        print(f"  Columns: {list(df.columns)}")
        if len(df) > 0:
            print(f"  First data row:")
            for i, col in enumerate(df.columns[:10]):
                val = df[col].iloc[0]
                print(f"    {i}: {col} = {val}")
    except Exception as e:
        print(f"Error with header row {header_row}: {e}")
