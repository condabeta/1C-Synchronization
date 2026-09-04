#!/usr/bin/env python
"""Check new Dekomo file for description field."""

from bs4 import BeautifulSoup

html_path = r"d:\projects\1C\Декомо\content_20_08_2026_22_51.xls"

with open(html_path, 'r', encoding='utf-8') as f:
    html_content = f.read()

soup = BeautifulSoup(html_content, 'html.parser')

# Find table
table = soup.find('table')
if table:
    first_row = table.find('tr')
    if first_row:
        cells = first_row.find_all(['td', 'th'])
        print(f"Total columns found: {len(cells)}")
        print(f"\nAll columns:")
        for i, cell in enumerate(cells):
            text = cell.get_text(strip=True)
            print(f"  {i}: {text}")
            
            # Check for description field
            if "description" in text.lower() or "дополнительное описание" in text.lower() or "additional description" in text.lower():
                print(f"    ^^^ FOUND DESCRIPTION FIELD AT COLUMN {i} ^^^")
