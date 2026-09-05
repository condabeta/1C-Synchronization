"""Content scraper for led-crystal.ru.

LED CRYSTAL gave written permission on 2026-09-05 to take photos and
descriptions from their public pages, and asked that the source be credited.
See docs/supplier_permissions.md.

The site is a uKit-style builder, not Bitrix: there is no /search/ and no
/catalog/ tree. Everything hangs off sitemap.xml as flat slugs, so this module
crawls the sitemap once, builds an index keyed by article, and answers lookups
from memory. 391 URLs against ~325 priced SKUs, so one pass covers the range.

robots.txt allows this; only /html/, /widgets/, /sitesearch*, /about$ and
/__404$ are disallowed, none of which we touch.
"""

from __future__ import annotations

import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from html import unescape
from pathlib import Path
from typing import Callable
from urllib.parse import quote, urljoin, urlparse, urlsplit, urlunsplit

BASE_URL = "https://led-crystal.ru"
SITEMAP_URL = f"{BASE_URL}/sitemap.xml"
USER_AGENT = "SvetoyarStagingBot/1.0 (+content sync, permission granted 2026-09-05)"
ATTRIBUTION = "Фото и описание — LED CRYSTAL (led-crystal.ru)"

REQUEST_TIMEOUT = 30
DEFAULT_DELAY = 0.5

SITEMAP_NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}

# Product images are the only ones marked up as schema.org/ImageObject. Site
# chrome (logo, the "найти дилера" and "стать партнёром" banners) repeats on
# every page and carries no contentUrl, so this selector excludes it by
# construction.
CONTENT_URL_RE = re.compile(r'<link itemprop="contentUrl" href="([^"]+)"')
OG_DESCRIPTION_RE = re.compile(r'<meta property="og:description" content="([^"]*)"', re.I)
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.I | re.S)

# Every page falls back to this site-wide blurb; it is not a product description.
BOILERPLATE_MARKER = "Официальный сайт LED CRYSTAL"
TITLE_SUFFIX_RE = re.compile(r"\s*[–—-]\s*LED CRYSTAL\s*$", re.I)

# Article codes: letters/digits with dashes and at least one digit,
# e.g. LR37-M, LB24-12A, R2011-07-WH, COB528.
CODE_TOKEN_RE = re.compile(r"[A-Z0-9]+(?:-[A-Z0-9]+)*", re.I)

CYRILLIC_LOOKALIKES = str.maketrans({
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O",
    "Р": "P", "С": "C", "Т": "T", "Х": "X", "У": "Y",
    "а": "a", "в": "b", "е": "e", "к": "k", "м": "m", "н": "h", "о": "o",
    "р": "p", "с": "c", "т": "t", "х": "x", "у": "y",
})


@dataclass
class SiteProductInfo:
    """Return shape kept for the existing crystal.py call site."""

    product_url: str | None
    images: list[str]
    description: str | None


@dataclass
class CrystalPage:
    url: str
    title: str
    description: str | None
    images: list[str] = field(default_factory=list)
    codes: set[str] = field(default_factory=set)


def _latinize(text: str) -> str:
    """Cyrillic lookalikes to Latin, uppercased, spacing left alone."""
    return (text or "").translate(CYRILLIC_LOOKALIKES).upper()


def normalize_sku_for_site(sku: str) -> str:
    """Latinize an article and drop its spaces (L49-EС -> L49-EC)."""
    return _latinize(sku).replace(" ", "").strip()


def _encode_url(url: str) -> str:
    """Percent-encode non-ASCII path/query; some sitemap slugs are Cyrillic."""
    parts = urlsplit(url)
    return urlunsplit((
        parts.scheme,
        parts.netloc.encode("idna").decode("ascii") if parts.netloc else "",
        quote(parts.path, safe="/%"),
        quote(parts.query, safe="=&%"),
        "",
    ))


def _fetch(url: str, timeout: int = REQUEST_TIMEOUT) -> str:
    request = urllib.request.Request(_encode_url(url), headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def fetch_sitemap_urls(sitemap_url: str = SITEMAP_URL) -> list[str]:
    root = ET.fromstring(_fetch(sitemap_url))
    urls = [
        node.findtext("s:loc", namespaces=SITEMAP_NS)
        for node in root.findall("s:url", SITEMAP_NS)
    ]
    return [url for url in urls if url]


def _clean_title(raw: str) -> str:
    return TITLE_SUFFIX_RE.sub("", unescape(" ".join(raw.split()))).strip()


def _extract_codes(title: str) -> set[str]:
    """Article-looking tokens in a title, normalized for lookup.

    The title is normalized *before* tokenizing: the site types Cyrillic
    lookalikes inside its own articles ("LC10S-ССT-01" has a Cyrillic С), and
    tokenizing first would split the code at that letter.
    """
    return {
        token
        for token in CODE_TOKEN_RE.findall(_latinize(title))
        if len(token) >= 3 and any(ch.isdigit() for ch in token)
    }


def parse_product_page(url: str, html: str) -> CrystalPage | None:
    """Parse one page, or None if it is not a product page.

    Category pages carry no schema.org image and only the site-wide blurb as
    their description, which is how they are told apart.
    """
    images = [urljoin(BASE_URL, unescape(path))
              for path in dict.fromkeys(CONTENT_URL_RE.findall(html))]
    if not images:
        return None

    title_match = TITLE_RE.search(html)
    title = _clean_title(title_match.group(1)) if title_match else ""
    if not title:
        return None

    description = None
    desc_match = OG_DESCRIPTION_RE.search(html)
    if desc_match:
        text = unescape(" ".join(desc_match.group(1).split())).strip()
        if text and BOILERPLATE_MARKER not in text:
            description = text

    return CrystalPage(
        url=url,
        title=title,
        description=description,
        images=images,
        codes=_extract_codes(title),
    )


def crawl(
    *,
    sitemap_url: str = SITEMAP_URL,
    delay: float = DEFAULT_DELAY,
    limit: int | None = None,
    cache_dir: Path | None = None,
    progress: Callable[[str], None] | None = None,
) -> list[CrystalPage]:
    """Fetch every sitemap URL once and return the product pages among them."""

    def report(message: str) -> None:
        if progress:
            progress(message)

    urls = fetch_sitemap_urls(sitemap_url)
    if limit:
        urls = urls[:limit]
    report(f"Sitemap: {len(urls)} URLs")

    pages: list[CrystalPage] = []
    skipped = failed = 0

    for index, url in enumerate(urls, start=1):
        cache_file = None
        if cache_dir:
            slug = urlparse(url).path.strip("/").replace("/", "_") or "index"
            cache_file = cache_dir / f"{slug}.html"

        try:
            if cache_file and cache_file.exists():
                html = cache_file.read_text(encoding="utf-8", errors="replace")
            else:
                if delay:
                    time.sleep(delay)
                html = _fetch(url)
                if cache_file:
                    cache_dir.mkdir(parents=True, exist_ok=True)
                    cache_file.write_text(html, encoding="utf-8")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            failed += 1
            report(f"  [{index}/{len(urls)}] failed {url}: {exc}")
            continue

        page = parse_product_page(url, html)
        if page is None:
            skipped += 1
            continue
        pages.append(page)

        if index % 50 == 0:
            report(f"  [{index}/{len(urls)}] products {len(pages)}, "
                   f"non-product {skipped}, failed {failed}")

    report(f"Crawl finished: {len(pages)} product pages, "
           f"{skipped} non-product, {failed} failed.")
    return pages


def _compact(text: str) -> str:
    """Drop everything but letters and digits, so LB114020--1-W == LB114020-1-W."""
    return re.sub(r"[^A-Z0-9]", "", normalize_sku_for_site(text))


@dataclass
class CrystalIndex:
    by_code: dict[str, CrystalPage] = field(default_factory=dict)
    by_compact: dict[str, CrystalPage] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.by_code)


def _unique(claims: dict[str, list[CrystalPage]]) -> dict[str, CrystalPage]:
    return {key: found[0] for key, found in claims.items() if len(found) == 1}


def build_index(pages: list[CrystalPage]) -> CrystalIndex:
    """Index pages by article code, exact and punctuation-stripped.

    A code claimed by more than one page is ambiguous - usually a profile code
    quoted in an accessory title - so it is dropped rather than guessed at.
    """
    exact: dict[str, list[CrystalPage]] = {}
    compact: dict[str, list[CrystalPage]] = {}
    for page in pages:
        for code in page.codes:
            exact.setdefault(code, []).append(page)
            compact.setdefault(_compact(code), []).append(page)

    return CrystalIndex(by_code=_unique(exact), by_compact=_unique(compact))


def lookup(sku: str, index: CrystalIndex) -> CrystalPage | None:
    """Exact article match, then a punctuation-insensitive one.

    The price list writes some articles differently from the site - a doubled
    dash, a Cyrillic letter that looks Latin - so an exact miss is retried
    against codes with punctuation stripped. Both stages compare whole codes:
    matching on substrings would give LB114020-1-W the page for
    LB114020-1-WW, which is a different colour temperature. Anything claimed
    by more than one page stays unmatched - a wrong photo is worse than none.
    """
    page = index.by_code.get(normalize_sku_for_site(sku))
    if page is not None:
        return page
    return index.by_compact.get(_compact(sku))


_INDEX: CrystalIndex | None = None


def load_index(
    *,
    refresh: bool = False,
    cache_dir: Path | None = None,
    progress: Callable[[str], None] | None = None,
) -> CrystalIndex:
    """Build the index once per process; the whole site is only ~391 pages."""
    global _INDEX
    if _INDEX is None or refresh:
        _INDEX = build_index(crawl(cache_dir=cache_dir, progress=progress))
    return _INDEX


def fetch_product_info(
    sku: str,
    *,
    progress: Callable[[str], None] | None = None,
    sleep_seconds: float = 0.0,
) -> SiteProductInfo:
    """Look up one article. Signature kept for crystal.py.

    The first call crawls the site; every later call is a memory lookup, so
    sleep_seconds is accepted and ignored.
    """
    try:
        index = load_index(progress=progress)
    except (urllib.error.URLError, TimeoutError, ET.ParseError) as exc:
        if progress:
            progress(f"  led-crystal.ru unavailable: {exc}")
        return SiteProductInfo(None, [], None)

    page = lookup(sku, index)
    if page is None:
        return SiteProductInfo(None, [], None)
    return SiteProductInfo(page.url, list(page.images), page.description)
