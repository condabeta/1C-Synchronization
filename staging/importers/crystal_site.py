from __future__ import annotations

import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from html import unescape
from typing import Callable
from urllib.parse import quote, urljoin

BASE_URL = "http://led-crystal.ru"
USER_AGENT = "SvetoyarStagingBot/1.0"
SKU_PATTERN = re.compile(r"^[A-ZА-Я0-9][A-ZА-Я0-9.\-/ ]+$", re.I)


@dataclass
class SiteProductInfo:
    product_url: str | None
    images: list[str]
    description: str | None


def normalize_sku_for_site(sku: str) -> str:
    return (
        sku.replace("С", "C")
        .replace("с", "c")
        .replace("А", "A")
        .replace("В", "B")
        .replace("Е", "E")
        .replace("К", "K")
        .replace("М", "M")
        .replace("Н", "H")
        .replace("О", "O")
        .replace("Р", "P")
        .replace("Т", "T")
        .replace("Х", "X")
        .strip()
    )


def _fetch(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def _extract_images(html: str) -> list[str]:
    images: list[str] = []
    og = re.search(r'property="og:image"\s+content="([^"]+)"', html, re.I)
    if og:
        images.append(unescape(og.group(1)))
    for match in re.findall(r'(?:src|href)="([^"]+\.(?:jpg|jpeg|png|webp)(?:\?[^"]*)?)"', html, re.I):
        if "logo" in match.lower() or "sprite" in match.lower():
            continue
        images.append(unescape(urljoin(BASE_URL, match)))
    return list(dict.fromkeys(images))


def _extract_description(html: str) -> str | None:
    for pattern in (
        r'itemprop="description"[^>]*>(.*?)</div>',
        r'class="[^"]*detail-text[^"]*"[^>]*>(.*?)</div>',
        r'class="[^"]*product-detail[^"]*"[^>]*>(.*?)</div>',
    ):
        match = re.search(pattern, html, re.I | re.S)
        if not match:
            continue
        text = unescape(re.sub(r"<[^>]+>", " ", match.group(1)))
        text = " ".join(text.split())
        if text:
            return text[:4000]
    return None


def _find_product_links(html: str, sku: str) -> list[str]:
    links: list[str] = []
    sku_upper = sku.upper()
    for href in re.findall(r'href="([^"]+)"', html, re.I):
        if sku_upper in href.upper() or sku_upper in unescape(href).upper():
            links.append(urljoin(BASE_URL, href))
    catalog_links = re.findall(r'href="(/catalog/[^"]+/)"', html, re.I)
    for href in catalog_links:
        links.append(urljoin(BASE_URL, href))
    return list(dict.fromkeys(links))


def fetch_product_info(
    sku: str,
    *,
    progress: Callable[[str], None] | None = None,
    sleep_seconds: float = 0.3,
) -> SiteProductInfo:
    if not SKU_PATTERN.match(sku):
        return SiteProductInfo(None, [], None)

    candidates = [sku, normalize_sku_for_site(sku)]
    candidates = list(dict.fromkeys(c for c in candidates if c))

    for candidate in candidates:
        search_url = f"{BASE_URL}/search/?q={quote(candidate)}"
        try:
            if sleep_seconds:
                time.sleep(sleep_seconds)
            search_html = _fetch(search_url)
        except (urllib.error.URLError, TimeoutError) as exc:
            if progress:
                progress(f"  site lookup failed for {sku}: {exc}")
            return SiteProductInfo(None, [], None)

        product_links = _find_product_links(search_html, candidate)
        for product_url in product_links[:3]:
            try:
                if sleep_seconds:
                    time.sleep(sleep_seconds)
                page_html = _fetch(product_url)
            except (urllib.error.URLError, TimeoutError):
                continue
            images = _extract_images(page_html)
            description = _extract_description(page_html)
            if images or description:
                return SiteProductInfo(product_url, images, description)

    return SiteProductInfo(f"{BASE_URL}/search/?q={quote(sku)}", [], None)
