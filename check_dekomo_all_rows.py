#!/usr/bin/env python
"""Check all rows in Dekomo file to find description field anywhere."""

from bs4 import BeautifulSoup

html_path = r"c:\Users\aaa\Downloads\Telegram Desktop\content_20_08_2026_12_34.xls"

with open(html_path, 'r', encoding='utf-8') as f:
    html_content = f.read()

soup = BeautifulSoup(html_content, 'html.parser')

# Find table
table = soup.find('table')
if table:
    rows = table.find_all('tr')
    
    # Check first 5 rows for any cell containing "описание"
    print("Checking first 5 rows for 'описание' or 'description':")
    for i, row in enumerate(rows[:5]):
        cells = row.find_all('td')
        for j, cell in enumerate(cells):
            text = cell.get_text(strip=True)
            if "описание" in text.lower() or "description" in text.lower():
                print(f"  Found in Row {i}, Column {j}: {text}")
