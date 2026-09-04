#!/usr/bin/env python
"""Test Crystal site scraping for descriptions."""

from staging.importers.crystal_site import fetch_product_info

# Test with a known SKU from Crystal
test_sku = "L39-S"

print(f"Testing Crystal site scraping for SKU: {test_sku}")
info = fetch_product_info(test_sku)

if info:
    print(f"  Product URL: {info.product_url}")
    print(f"  Images: {len(info.images)}")
    print(f"  Description: {info.description[:200] if info.description else 'NULL'}...")
else:
    print("  No info returned")
