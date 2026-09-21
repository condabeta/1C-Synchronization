"""Content scraper for stz-salux.ru.

Salux sends a price list and nothing else: no photos, no descriptions, no
export of either. Their own site is the only place that content exists, and the
client asked for it to be taken from there (Юлия, 21.09.2026).

The site is Bitrix, and it publishes one page per *series* - «ССдВз 1Ех
«Агат»» - not one per article. That suits the price list, where a series is a
block of articles that differ only in wattage: one description and one photo set
cover the whole block. Matching is therefore (family, series): the family comes
from the order marking the price list already carries, the series from the
product name.

Pages are found by walking /products/ into its sections rather than through
sitemap.xml, which was last written in 2024 and misses eleven pages, among them
the Гранит, Оникс and Сегмент series.

robots.txt allows all of this: it disallows /bitrix/, index.php and a list of
sorting, printing and login parameters, none of which are touched here.

Permission to republish is *not* confirmed yet - see docs/supplier_permissions.md.
Everything fetched is credited to Salux in `content_attribution`, so any card
built from it can be found again if they object.
"""

from __future__ import annotations

import re
import time
import urllib.request
from dataclasses import dataclass, field
from html import unescape
from typing import Callable, Iterable
from urllib.parse import urljoin

from staging.certificates import parse_salux_marking
from staging.pricing import fold

BASE_URL = "https://stz-salux.ru"
CATALOG_URL = f"{BASE_URL}/products/"
USER_AGENT = "SvetoyarStagingBot/1.0 (+content sync for a Salux distributor)"
ATTRIBUTION = "Фото и описание — СТЗ «САЛЮКС» (stz-salux.ru)"

REQUEST_TIMEOUT = 30
DEFAULT_DELAY = 0.5

SECTION_RE = re.compile(r'href="(/products/[a-z0-9\-]+/)"', re.I)
PAGE_RE = re.compile(r'href="(/products/[^"#?]+\.html)"', re.I)

H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S)
# The gallery, and only the gallery: full-size photos hang off fancybox links
# inside <div class="slider">. Drawings, mounting diagrams and light
# distribution curves live further down the page in blocks of their own, and the
# site chrome (logo, social icons) carries no fancybox link at all.
SLIDER_RE = re.compile(r'<div class="slider">(.*?)<div class="sliderControls"', re.S)
GALLERY_HREF_RE = re.compile(r'<a[^>]+class="fancybox"[^>]+href="([^"]+)"', re.I)
DESCRIPTION_RE = re.compile(r'<div class="descriptionProducts">(.*?)</div>', re.S)

# "Взрывозащищённый светодиодный светильник серии ССдВз 1Ех «Агат»". The family
# is written the way the order markings are, so it needs no translation; the
# series name is quoted, in either « » or " ".
TITLE_RE = re.compile(
    r"серии\s+(?P<family>[А-Яа-яЁё]+(?:\s+\d?[ЕE][хx])?)\s*"
    r"[«\"](?P<series>[^»\"]+)[»\"]"
)

# The price list writes the explosion-proof line without 1Ex as "ССдВз Ех"; the
# site's own pages just say "ССдВз". Same products, two spellings of the family.
FAMILY_ALIASES = {"ссдвз": "ссдвз ех"}

# The marine version of a luminaire is a different product at a different price
# that shares its order marking: "ССдВз 1Ех 02 db-010-006 «Агат 10 1Ех»" is
# 14 250 ₽ on the ССдВз 1Ех sheet and 14 962 ₽ on the ССдС sheet, and the photos
# differ too - orange industrial body against a white marine one (client,
# 21.09.2026). The site keeps them apart only in the heading, which starts
# "Судовой", so that word is part of the key.
MARINE_RE = re.compile(r"судов", re.I)
MARINE_SHEET_PREFIX = "ссдс"

# Placeholder pages: a section entry that was never filled in. They keep the
# site's generic title and carry only chrome images.
STUB_TITLE = "Светодиодная продукция"


@dataclass
class SaluxPage:
    url: str
    title: str
    family: str
    series: str
    description: str | None
    images: list[str] = field(default_factory=list)
    marine: bool = False

    @property
    def key(self) -> tuple[str, str, bool]:
        family = fold(self.family)
        return (FAMILY_ALIASES.get(family, family), fold(self.series), self.marine)


def _fetch(url: str, timeout: int = REQUEST_TIMEOUT) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def _text(markup: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", markup)).replace("\xa0", " ").split())


def discover_pages(delay: float = DEFAULT_DELAY) -> list[str]:
    """Every product page, found by walking the catalogue sections."""
    catalogue = _fetch(CATALOG_URL)
    sections = sorted({urljoin(BASE_URL, path) for path in SECTION_RE.findall(catalogue)})

    pages: list[str] = []
    seen: set[str] = set()
    for section in sections:
        if section.rstrip("/") == CATALOG_URL.rstrip("/"):
            continue
        for path in PAGE_RE.findall(_fetch(section)):
            url = urljoin(BASE_URL, path)
            if url not in seen:
                seen.add(url)
                pages.append(url)
        time.sleep(delay)
    return pages


def parse_product_page(url: str, markup: str) -> SaluxPage | None:
    """A page with a series in its heading, or None for the placeholder pages."""
    headings = H1_RE.findall(markup)
    if not headings:
        return None
    title = _text(headings[0])
    if title.startswith(STUB_TITLE):
        return None
    match = TITLE_RE.search(title)
    if not match:
        return None

    slider = SLIDER_RE.search(markup)
    images = []
    if slider:
        for href in GALLERY_HREF_RE.findall(slider.group(1)):
            image = urljoin(BASE_URL, href)
            if image not in images:
                images.append(image)

    description = DESCRIPTION_RE.search(markup)
    return SaluxPage(
        url=url,
        title=title,
        family=match.group("family"),
        series=match.group("series"),
        description=_text(description.group(1)) if description else None,
        images=images,
        marine=bool(MARINE_RE.match(title)),
    )


def crawl(
    urls: Iterable[str] | None = None,
    *,
    delay: float = DEFAULT_DELAY,
    progress: Callable[[str], None] | None = None,
) -> list[SaluxPage]:
    pages: list[SaluxPage] = []
    targets = list(urls) if urls is not None else discover_pages(delay=delay)
    for index, url in enumerate(targets, start=1):
        try:
            page = parse_product_page(url, _fetch(url))
        except Exception as exc:  # a single unreachable page must not stop the crawl
            if progress:
                progress(f"  [{index}/{len(targets)}] {url} - ошибка: {exc}")
            continue
        if page:
            pages.append(page)
            if progress:
                progress(
                    f"  [{index}/{len(targets)}] {page.family} «{page.series}» - "
                    f"{len(page.images)} фото, описание {'есть' if page.description else 'нет'}"
                )
        elif progress:
            progress(f"  [{index}/{len(targets)}] {url.rsplit('/', 1)[-1]} - страница-заготовка")
        time.sleep(delay)
    return pages


def build_index(pages: list[SaluxPage]) -> dict[tuple[str, str, bool], SaluxPage]:
    """(family, series, marine) -> page. The richer page wins a repeated key."""
    index: dict[tuple[str, str, bool], SaluxPage] = {}
    for page in pages:
        current = index.get(page.key)
        if current is None or len(page.images) > len(current.images):
            index[page.key] = page
    return index


# The name in the price list leads with the series: «Флагман 10», «Агат 1Ех
# Е27», «Офис 40». Quotes around it are the supplier's, and inconsistent.
NAME_SERIES_RE = re.compile(r'^[«"\s]*([А-Яа-яЁёA-Za-z]+(?:\s+room)?)')


def series_of(name: str | None) -> str | None:
    match = NAME_SERIES_RE.match(name or "")
    return match.group(1) if match else None


def is_marine(product: dict) -> bool:
    """Whether a price row is the marine version.

    The ССдС sheet is the marine catalogue. Its own series carry ССдС markings,
    but «Агат» sits there under a ССдВз 1Ех marking - the same marking as the
    industrial one - so the sheet has to be asked, not the marking alone.
    """
    sheet = fold(product.get("sheet"))
    marking = parse_salux_marking(product.get("supplier_sku") or "")
    return sheet.startswith(MARINE_SHEET_PREFIX) or bool(marking and marking[0] == "ссдс")


def lookup(product: dict, index: dict[tuple[str, str, bool], SaluxPage]) -> SaluxPage | None:
    """The page for a price row, by its marking's family and its name's series."""
    marking = parse_salux_marking(product.get("supplier_sku") or "")
    series = series_of(product.get("name"))
    if not marking or not series:
        return None
    marine = is_marine(product)
    # Only one version of most series exists, and then the heading need not say
    # which it is - so a miss falls back to the other one rather than losing the
    # page. Where both exist, the exact key above has already answered.
    return index.get((marking[0], fold(series), marine)) or index.get(
        (marking[0], fold(series), not marine)
    )
