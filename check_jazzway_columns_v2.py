#!/usr/bin/env python
"""Check Jazzway Excel columns with different header rows."""

import pandas as pd

excel_path = r"D:\projects\1C\Джазвея\11.08 Остатки для клиента.xlsx"

# Check first 5 rows to find header
for header_row in range(5):
    try:
        df = pd.read_excel(excel_path, header=header_row)
        print(f"\nHeader row {header_row}:")
        print(f"  Columns: {list(df.columns)}")
        if len(df) > 0:
            print(f"  First data row sample:")
            for col in df.columns[:10]:
                val = df[col].iloc[0]
                print(f"    {col}: {val}")
    except Exception as e:
        print(f"Error with header row {header_row}: {e}")
