#!/usr/bin/env python
"""Check if description data exists in Dekomo HTML file."""

from bs4 import BeautifulSoup

html_path = r"d:\projects\1C\Декомо\content_20_08_2026_22_51.xls"

with open(html_path, 'r', encoding='utf-8') as f:
    html_content = f.read()

soup = BeautifulSoup(html_content, 'html.parser')

# Find table
table = soup.find('table')
if table:
    rows = table.find_all('tr')
    
    # Get headers
    header_row = rows[0]
    headers = [cell.get_text(strip=True) for cell in header_row.find_all('td')]
    
    # Find description column index
    desc_col_idx = None
    for i, header in enumerate(headers):
        if "Дополнительное описание" in header:
            desc_col_idx = i
            print(f"Found description column at index {i}: {header}")
            break
    
    if desc_col_idx is not None:
        # Check first 10 data rows for description content
        print(f"\nChecking first 10 data rows for description content:")
        for i, row in enumerate(rows[1:11]):
            cells = row.find_all('td')
            if desc_col_idx < len(cells):
                desc_text = cells[desc_col_idx].get_text(strip=True)
                sku = cells[1].get_text(strip=True) if len(cells) > 1 else "N/A"
                print(f"  Row {i}: SKU={sku}, Description={desc_text[:80] if desc_text else '(empty)'}")
    else:
        print("Description column not found in headers")
