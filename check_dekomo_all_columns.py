#!/usr/bin/env python
"""Check all columns in Dekomo HTML file."""

from bs4 import BeautifulSoup

html_path = r"D:\projects\1C\Декомо\content_20_08_2026_12_34.xls"

with open(html_path, 'r', encoding='utf-8') as f:
    html_content = f.read()

soup = BeautifulSoup(html_content, 'html.parser')

# Find table
table = soup.find('table')
if table:
    first_row = table.find('tr')
    if first_row:
        cells = first_row.find_all('td')
        print(f"Total columns: {len(cells)}")
        print(f"\nAll columns:")
        for i, td in enumerate(cells):
            text = td.get_text(strip=True)
            print(f"  {i}: {text}")
