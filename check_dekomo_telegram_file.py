#!/usr/bin/env python
"""Check Dekomo file from Telegram for description field."""

from bs4 import BeautifulSoup

html_path = r"c:\Users\aaa\Downloads\Telegram Desktop\content_20_08_2026_12_34.xls"

with open(html_path, 'r', encoding='utf-8') as f:
    html_content = f.read()

soup = BeautifulSoup(html_content, 'html.parser')

# Find table
table = soup.find('table')
if table:
    rows = table.find_all('tr')
    
    # Check header row for all columns
    header_row = rows[0]
    cells = header_row.find_all('td')
    print(f"Header row has {len(cells)} columns")
    print(f"\nAll column headers:")
    for i, cell in enumerate(cells):
        text = cell.get_text(strip=True)
        print(f"  {i}: {text}")
        
        # Check if any column contains "описание" (description)
        if "описание" in text.lower() or "description" in text.lower():
            print(f"    ^^^ FOUND DESCRIPTION FIELD ^^^")
