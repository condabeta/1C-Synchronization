#!/usr/bin/env python
"""Check Dekomo HTML file for description field."""

from bs4 import BeautifulSoup

html_path = r"D:\projects\1C\Декомо\content_20_08_2026_12_34.xls"

with open(html_path, 'r', encoding='utf-8') as f:
    html_content = f.read()

soup = BeautifulSoup(html_content, 'html.parser')

# Find table headers
tables = soup.find_all('table')
print(f"Found {len(tables)} tables")

if tables:
    table = tables[0]
    headers = table.find_all('th')
    if headers:
        print(f"\nTable headers:")
        for i, th in enumerate(headers):
            print(f"  {i}: {th.get_text(strip=True)}")
    else:
        # Try finding td in first row as headers
        first_row = table.find('tr')
        if first_row:
            cells = first_row.find_all('td')
            print(f"\nFirst row cells (as headers):")
            for i, td in enumerate(cells):
                print(f"  {i}: {td.get_text(strip=True)}")
