#!/usr/bin/env python
"""Check sample data from Dekomo HTML file."""

from bs4 import BeautifulSoup

html_path = r"D:\projects\1C\Декомо\content_20_08_2026_12_34.xls"

with open(html_path, 'r', encoding='utf-8') as f:
    html_content = f.read()

soup = BeautifulSoup(html_content, 'html.parser')

# Find table
table = soup.find('table')
if table:
    rows = table.find_all('tr')
    print(f"Total rows: {len(rows)}")
    
    # Show first 3 data rows (skip header)
    print(f"\nFirst 3 data rows:")
    for i, row in enumerate(rows[1:4]):
        cells = row.find_all('td')
        print(f"\nRow {i}:")
        for j, cell in enumerate(cells):
            text = cell.get_text(strip=True)
            if text:
                print(f"  Column {j}: {text[:80]}...")
