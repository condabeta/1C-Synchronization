from __future__ import annotations

import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable

from pymysql.connections import Connection

from staging.config import SWG_YML_URL
from staging.importers.common import (
    ImportStats,
    bytes_sha256,
    clean,
    close_stale_runs,
    content_hash,
    count_supplier_products,
    finish_import_run,
    get_supplier_and_source,
    log_import_error,
    parse_decimal,
    start_import_run,
    flush_product_batch,
)

SUPPLIER_CODE = "swg"
SOURCE_CODE = "yml_export"
DEFAULT_YML_URL = SWG_YML_URL
DEFAULT_YML_PATH = r"D:\projects\1C\SWG\yml.xml"
BATCH_SIZE = 500


def _report(progress: Callable[[str], None] | None, message: str) -> None:
    if progress:
        progress(message)
    else:
        print(message, flush=True)


def fetch_yml(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def _category_map(shop: ET.Element) -> dict[str, str]:
    categories: dict[str, str] = {}
    for node in shop.findall("./categories/category"):
        category_id = node.get("id")
        if category_id and node.text:
            categories[category_id] = node.text.strip()
    return categories


def _offer_params(offer: ET.Element) -> dict[str, str]:
    params: dict[str, str] = {}
    for param in offer.findall("param"):
        name = param.get("name")
        if name and param.text:
            params[name.strip()] = param.text.strip()
    return params


def _offer_pictures(offer: ET.Element) -> list[str]:
    images: list[str] = []
    seen: set[str] = set()
    for node in offer.findall("picture"):
        if node.text:
            url = node.text.strip()
            if url and url not in seen:
                seen.add(url)
                images.append(url)
    return images


def _child_text(offer: ET.Element, tag: str) -> str | None:
    node = offer.find(tag)
    if node is None or not node.text:
        return None
    return node.text.strip()


def normalize_offer(
    offer: ET.Element,
    categories: dict[str, str],
    *,
    store_raw: bool = False,
) -> dict[str, Any] | None:
    sku = clean(_child_text(offer, "vendorCode") or offer.get("id"))
    if not sku:
        return None

    params = _offer_params(offer)
    category_id = clean(_child_text(offer, "categoryId"))
    category_name = categories.get(category_id or "", category_id)
    price = parse_decimal(_child_text(offer, "price"))
    available = offer.get("available", "true").lower() in {"true", "1", "yes"}
    images = _offer_pictures(offer)
    brand = clean(params.get("Бренд"))

    normalized = {
        "supplier_sku": sku,
        "supplier_sku_raw": sku,
        "name": clean(_child_text(offer, "model")),
        "brand": brand,
        "manufacturer_code": sku,
        "supplier_category": category_name,
        # The feed's categories are a flat list with no parentId, so the path is
        # the name itself. It used to hold the raw id ("00-00037304"), which is
        # what the markup rules and the category tree then tried to match on.
        "supplier_category_path": category_name,
        "price": price,
        "price_retail": price,
        "price_old": None,
        "stock_qty": None,
        "is_available": 1 if available else 0,
        "product_url": clean(_child_text(offer, "url")),
        "barcode": clean(_child_text(offer, "barcode")),
        "images_json": images,
        "attributes_json": {**params, "category_id": category_id} if category_id else params,
        "raw_data_json": None,
    }
    if store_raw:
        normalized["raw_data_json"] = {
            "id": offer.get("id"),
            "available": offer.get("available"),
            "categoryId": category_id,
            "params": params,
            "pictures": images,
        }

    normalized["content_hash"] = content_hash(
        {
            "supplier_sku": sku,
            "name": normalized["name"],
            "brand": brand,
            "price": str(price) if price is not None else None,
            "is_available": normalized["is_available"],
            "category_id": category_id,
            "images_json": images,
            "attributes_json": params,
        }
    )
    return normalized


def parse_swg_yml(
    xml_bytes: bytes,
    *,
    limit: int | None = None,
    store_raw: bool = False,
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    root = ET.fromstring(xml_bytes)
    shop = root.find("shop")
    if shop is None:
        raise ValueError("Invalid SWG YML: missing <shop>")

    categories = _category_map(shop)
    products: list[dict[str, Any]] = []
    for offer in shop.findall("./offers/offer"):
        item = normalize_offer(offer, categories, store_raw=store_raw)
        if item:
            products.append(item)
        if limit is not None and len(products) >= limit:
            break
    return categories, products


def import_swg_yml(
    conn: Connection,
    url: str | None = None,
    yml_path: str | None = None,
    limit: int | None = None,
    progress: Callable[[str], None] | None = None,
    store_raw: bool = False,
) -> ImportStats:
    if yml_path:
        _report(progress, f"Reading SWG YML from file: {yml_path}")
        xml_bytes = Path(yml_path).read_bytes()
        source_label = str(yml_path)
    else:
        url = url or DEFAULT_YML_URL
        _report(progress, f"Downloading SWG YML feed...")
        xml_bytes = fetch_yml(url)
        source_label = url
    source_hash = bytes_sha256(xml_bytes)

    _report(progress, "Parsing YML...")
    categories, products = parse_swg_yml(xml_bytes, limit=limit, store_raw=store_raw)
    _report(progress, f"Parsed {len(products):,} offers, {len(categories):,} categories.")

    supplier_id, source_id = get_supplier_and_source(conn, SUPPLIER_CODE, SOURCE_CODE)
    close_stale_runs(conn, supplier_id)
    run_id = start_import_run(conn, supplier_id, source_id, source_label, source_hash)
    conn.commit()

    stats = ImportStats()
    existing_hashes: dict[str, str] = {}
    batch: list[dict[str, Any]] = []
    batch_number = 0

    try:
        for index, item in enumerate(products, start=1):
            stats.rows_total += 1
            batch.append(item)

            if len(batch) >= BATCH_SIZE:
                batch_number += 1
                flush_product_batch(conn, supplier_id, run_id, batch, existing_hashes, stats)
                _report(
                    progress,
                    (
                        f"  batch {batch_number}: offers {stats.rows_total:,}/{len(products):,} | "
                        f"db {count_supplier_products(conn, supplier_id):,} | "
                        f"new {stats.rows_imported:,} | updated {stats.rows_updated:,} | "
                        f"unchanged {stats.rows_skipped:,}"
                    ),
                )

        if batch:
            batch_number += 1
            flush_product_batch(conn, supplier_id, run_id, batch, existing_hashes, stats)
            _report(
                progress,
                (
                    f"  batch {batch_number}: offers {stats.rows_total:,}/{len(products):,} | "
                    f"db {count_supplier_products(conn, supplier_id):,} | "
                    f"new {stats.rows_imported:,} | updated {stats.rows_updated:,} | "
                    f"unchanged {stats.rows_skipped:,}"
                ),
            )

        status = "success" if stats.rows_errors == 0 else "partial"
        finish_import_run(conn, run_id, stats, status, None)
        conn.commit()
        _report(progress, "Import completed.")
        return stats
    except Exception as exc:
        conn.rollback()
        finish_import_run(conn, run_id, stats, "failed", str(exc))
        conn.commit()
        raise
