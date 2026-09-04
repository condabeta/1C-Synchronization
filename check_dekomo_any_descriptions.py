#!/usr/bin/env python
"""Check if any Dekomo products have descriptions."""

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
            break
    
    if desc_col_idx is not None:
        # Check all rows for description content
        has_description = 0
        total = 0
        examples = []
        
        for i, row in enumerate(rows[1:], start=1):
            cells = row.find_all('td')
            if desc_col_idx < len(cells):
                desc_text = cells[desc_col_idx].get_text(strip=True)
                sku = cells[1].get_text(strip=True) if len(cells) > 1 else "N/A"
                total += 1
                if desc_text:
                    has_description += 1
                    if len(examples) < 5:
                        examples.append((sku, desc_text[:100]))
        
        print(f"Dekomo Description Analysis:")
        print(f"  Total products: {total}")
        print(f"  Products with descriptions: {has_description} ({has_description/total*100:.2f}%)")
        print(f"  Products without descriptions: {total - has_description} ({(total-has_description)/total*100:.2f}%)")
        
        if examples:
            print(f"\nExamples of products with descriptions:")
            for sku, desc in examples:
                print(f"  {sku}: {desc}")
        else:
            print(f"\nNo products found with descriptions in the file.")
