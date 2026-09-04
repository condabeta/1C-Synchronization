#!/usr/bin/env python
"""Check Jazzway Excel columns."""

import pandas as pd

excel_path = r"D:\projects\1C\Джазвея\11.08 Остатки для клиента.xlsx"

df = pd.read_excel(excel_path, header=0)

print("Jazzway Excel columns:")
print(f"  Total columns: {len(df.columns)}")
print(f"  Column names: {list(df.columns)}")

print("\nFirst row sample:")
for col in df.columns:
    val = df[col].iloc[0] if len(df) > 0 else None
    print(f"  {col}: {val}")
