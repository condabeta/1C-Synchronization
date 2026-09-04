#!/usr/bin/env python
"""Check new Dekomo file for description field."""

import pandas as pd

excel_path = r"D:\projects\1C\Декомо\content_20_08_2026_12_34.xls"

# Check first 10 rows to find header
for header_row in range(10):
    try:
        df = pd.read_excel(excel_path, header=header_row, engine='xlrd')
        print(f"\nHeader row {header_row}:")
        print(f"  Columns: {list(df.columns)}")
        if len(df) > 0:
            print(f"  First data row sample:")
            for i, col in enumerate(df.columns[:15]):
                val = df[col].iloc[0]
                print(f"    {i}: {col} = {val}")
    except Exception as e:
        print(f"Error with header row {header_row}: {e}")
