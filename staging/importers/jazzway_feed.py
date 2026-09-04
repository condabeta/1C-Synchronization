"""Jazzway YML content feed (https://www.jazz-way.com/bitrix/catalog_export/export_all.xml).

The feed carries what the daily price XLSX does not: descriptions, the full
picture set, specs and links to certificates. It does *not* carry usable prices
(every offer is <price>1</price>) or stock, so the XLSX stays the source of
truth for those.

Offers are keyed by the "Код для заказа" param. That is the price file's
"Артикул" without its leading dot - the feed has no vendorCode tag at all.
"""

from __future__ import annotations

import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

DEFAULT_FEED_URL = "https://www.jazz-way.com/bitrix/catalog_export/export_all.xml"
USER_AGENT = "Mozilla/5.0 (compatible; SvetoyarImport/1.0)"
FETCH_TIMEOUT = 120

ORDER_CODE_PARAM = "Код для заказа"
BARCODE_PARAM = "Штрих-код"
ARTICLE_PARAM = "Артикул"
PICTURE_PARAM_RE = re.compile(r"^picture\d+$")
DOCUMENT_PARAM_RE = re.compile(r"^Документация \((?P<kind>[^)]+)\)")


@dataclass
class JazzwayContent:
    """Content for one offer, keyed by order code."""

    order_code: str
    offer_id: str | None = None
    article: str | None = None
    name: str | None = None
    description: str | None = None
    product_url: str | None = None
    barcode: str | None = None
    category: str | None = None
    category_path: str | None = None
    is_available: int | None = None
    images: list[str] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)
    documents: dict[str, list[str]] = field(default_factory=dict)


def fetch_feed(url: str = DEFAULT_FEED_URL, cache_path: Path | None = None) -> bytes:
    """Download the feed, optionally writing a copy to cache_path."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT) as response:
        payload = response.read()
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(payload)
    return payload


def _category_paths(categories: Iterable[ET.Element]) -> tuple[dict[str, str], dict[str, str]]:
    names: dict[str, str] = {}
    parents: dict[str, str | None] = {}
    for node in categories:
        cid = node.get("id")
        if not cid:
            continue
        names[cid] = (node.text or "").strip()
        parents[cid] = node.get("parentId")

    paths: dict[str, str] = {}
    for cid in names:
        chain, cursor, guard = [], cid, 0
        while cursor and cursor in names and guard < 20:
            chain.append(names[cursor])
            cursor = parents.get(cursor)
            guard += 1
        paths[cid] = " / ".join(reversed(chain))
    return names, paths


def _is_image_url(value: str | None) -> bool:
    """169 pictureN params are the bare domain, used as a 'no image' placeholder."""
    if not value:
        return False
    path = urllib.parse.urlparse(value).path
    return "." in path.rsplit("/", 1)[-1]


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_feed(source: bytes | str | Path) -> dict[str, JazzwayContent]:
    """Parse the feed into {order_code: JazzwayContent}."""
    if isinstance(source, bytes):
        root = ET.fromstring(source)
    else:
        root = ET.parse(str(source)).getroot()

    shop = root.find("shop")
    if shop is None:
        raise ValueError("Jazzway feed has no <shop> element")

    category_names, category_paths = _category_paths(shop.find("categories") or [])

    index: dict[str, JazzwayContent] = {}
    for offer in shop.find("offers") or []:
        params: dict[str, list[str]] = {}
        for node in offer.findall("param"):
            name = node.get("name")
            value = _clean(node.text)
            if name and value:
                params.setdefault(name, []).append(value)

        order_code = params.get(ORDER_CODE_PARAM, [None])[0]
        if not order_code:
            continue

        images: list[str] = []
        candidates = [_clean(node.text) for node in offer.findall("picture")]
        for name, values in params.items():
            if PICTURE_PARAM_RE.match(name):
                candidates.extend(values)
        for value in candidates:
            if _is_image_url(value) and value not in images:
                images.append(value)

        documents: dict[str, list[str]] = {}
        attributes: dict[str, Any] = {}
        for name, values in params.items():
            if PICTURE_PARAM_RE.match(name) or name in {ORDER_CODE_PARAM, BARCODE_PARAM}:
                continue
            doc = DOCUMENT_PARAM_RE.match(name)
            if doc:
                documents.setdefault(doc.group("kind"), []).extend(values)
                continue
            attributes[name] = values[0] if len(values) == 1 else values

        category_id = offer.findtext("categoryId")
        available = offer.get("available")

        index[order_code] = JazzwayContent(
            order_code=order_code,
            offer_id=offer.get("id"),
            article=params.get(ARTICLE_PARAM, [None])[0],
            name=_clean(offer.findtext("name")),
            description=_clean(offer.findtext("description")),
            product_url=_clean(offer.findtext("url")),
            barcode=params.get(BARCODE_PARAM, [None])[0],
            category=category_names.get(category_id or ""),
            category_path=category_paths.get(category_id or ""),
            is_available=None if available is None else int(available == "true"),
            images=images,
            attributes=attributes,
            documents=documents,
        )
    return index


def load_feed_index(
    url: str | None = DEFAULT_FEED_URL,
    file_path: Path | None = None,
    cache_path: Path | None = None,
) -> dict[str, JazzwayContent]:
    """Build the content index from a local file if given, otherwise the URL."""
    if file_path:
        return parse_feed(file_path)
    return parse_feed(fetch_feed(url or DEFAULT_FEED_URL, cache_path=cache_path))


def order_code_for(supplier_sku: str) -> str:
    """Price-file article -> feed order code ('.5040717' -> '5040717')."""
    return (supplier_sku or "").strip().lstrip(".")
