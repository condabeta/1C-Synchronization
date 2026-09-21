"""Photos embedded in the Salux price workbook.

The site covers most series but not all: 26 products on the «ССдО Линия» sheet,
the КСдУ complexes and the Ex cable entries have no page there. The client
pointed out that the price list itself carries pictures - the «Изображение»
column is not empty, it holds images anchored into the sheet - so this reads
them out.

They are anchored per block, not per row: one photo (sometimes several) sits
beside the first rows of a series and stands for the whole of it, exactly as
the site's series pages do. So an image is matched to the block whose rows
surround its anchor, and every article of that block gets it.

Files are written out under a folder named after the sheet, with the content
hash in the name, so re-running produces the same paths and a picture reused by
several blocks is stored once.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import openpyxl

from staging.importers.salux import iter_salux_rows

# An image is placed against the top of its block, and can sit a row or two
# above the first article - it is anchored to the picture column, beside the
# group title. Anything further above belongs to the previous block.
ANCHOR_LEAD = 3

EXTENSIONS = {"jpeg": "jpg", "wdp": "jpg"}


@dataclass
class SheetImage:
    sheet: str
    row: int
    data: bytes
    extension: str

    @property
    def digest(self) -> str:
        return hashlib.sha1(self.data).hexdigest()[:16]

    def filename(self) -> str:
        return f"{self.digest}.{EXTENSIONS.get(self.extension, self.extension)}"


@dataclass
class Match:
    sheet: str
    group: str | None
    rows: tuple[int, int]
    skus: list[str] = field(default_factory=list)
    images: list[SheetImage] = field(default_factory=list)


def read_images(xlsx_path: Path) -> list[SheetImage]:
    """Every picture in the workbook, with the sheet and row it is anchored to."""
    workbook = openpyxl.load_workbook(xlsx_path)
    images: list[SheetImage] = []
    for name in workbook.sheetnames:
        sheet = workbook[name]
        for image in getattr(sheet, "_images", []):
            anchor = getattr(image.anchor, "_from", None)
            if anchor is None:
                continue
            data = image._data()
            images.append(
                SheetImage(
                    sheet=name,
                    row=anchor.row + 1,  # openpyxl counts from zero here
                    data=data,
                    extension=(image.format or "png").lower(),
                )
            )
    return images


def _blocks(xlsx_path: Path) -> list[Match]:
    """Every block of articles, with the rows it spans and the SKUs in it.

    Read through iter_salux_rows rather than the raw sheets, so the SKUs here
    are the ones the database holds - including the 102 that are split apart
    because they share a marking with another product.
    """
    blocks: dict[tuple[str, str | None], Match] = {}
    for item in iter_salux_rows(xlsx_path):
        attrs = item["attributes_json"]
        sheet, group = attrs.get("sheet"), attrs.get("group")
        row = item.get("source_row_number")
        if not sheet or row is None:
            continue
        key = (sheet, group)
        match = blocks.get(key)
        if match is None:
            match = blocks[key] = Match(sheet=sheet, group=group, rows=(row, row))
        match.rows = (min(match.rows[0], row), max(match.rows[1], row))
        match.skus.append(item["supplier_sku"])
    return list(blocks.values())


def match_images(xlsx_path: Path) -> tuple[list[Match], list[SheetImage]]:
    """Blocks with their pictures, and the pictures that belong to no block."""
    blocks = _blocks(xlsx_path)
    images = read_images(xlsx_path)

    by_sheet: dict[str, list[Match]] = defaultdict(list)
    for block in blocks:
        by_sheet[block.sheet].append(block)
    for sheet_blocks in by_sheet.values():
        sheet_blocks.sort(key=lambda block: block.rows[0])

    unmatched: list[SheetImage] = []
    for image in images:
        candidates = by_sheet.get(image.sheet, [])
        chosen = None
        for block in candidates:
            first, last = block.rows
            if first - ANCHOR_LEAD <= image.row <= last:
                chosen = block
                break
        if chosen is None:
            # Anchored past the last article of its sheet: it belongs to the
            # block above it, which is the last one that starts before it.
            above = [block for block in candidates if block.rows[0] <= image.row]
            chosen = above[-1] if above else None
        if chosen is None:
            unmatched.append(image)
        else:
            chosen.images.append(image)
    return blocks, unmatched


def save_images(blocks: Iterable[Match], out_dir: Path) -> dict[str, list[str]]:
    """Write the pictures out and return {supplier_sku: [path, ...]}."""
    out_dir.mkdir(parents=True, exist_ok=True)
    per_sku: dict[str, list[str]] = {}
    written: set[Path] = set()

    for block in blocks:
        if not block.images or not block.skus:
            continue
        paths = []
        for image in block.images:
            folder = out_dir / image.sheet.replace("/", "-")
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / image.filename()
            if path not in written and not path.exists():
                path.write_bytes(image.data)
            written.add(path)
            paths.append(str(path))
        for sku in block.skus:
            per_sku.setdefault(sku, []).extend(paths)
    return per_sku
