#!/usr/bin/env python
"""Bring the catalogue's photographs onto our own disk.

Every picture in the catalogue is currently a link to a supplier's server -
615,059 rows, 582,489 distinct URLs. That is fragile in three ways: the
supplier can take an image down (Dekomo did exactly that this week, under a
legal claim), can block hotlinking at any time, and is under no obligation to
keep serving our customers' page loads. The site itself keeps its own files;
ours should too.

    python scripts/download_images.py --limit 1000        # a first batch
    python scripts/download_images.py --supplier dekomo   # one supplier
    python scripts/download_images.py                     # everything left

Safe to stop and restart: a row is claimed only once its file is on disk, so a
second run picks up where the first was interrupted.

**Images are resized.** At full size the set is around 290 GB, which does not
fit on this machine, and a 2.8 MB photograph helps nobody on a product page.
Anything larger than 1600px on its long side is scaled down and saved as JPEG,
which brings the set to a manageable size and loads faster for customers.
Transparency is kept as PNG - it matters for product cut-outs.

**The same picture is stored once.** Files are named by the hash of their
contents, so the URL that 271 products share costs one file, and a supplier
re-issuing the same photo under a new URL costs nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit, quote

from PIL import Image, UnidentifiedImageError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from staging.db import db_session, fetch_all, fetch_one

IMAGES_DIR = Path(r"D:\projects\1C\images")
MAX_SIDE = 1600
JPEG_QUALITY = 85
USER_AGENT = "SvetoyarStagingBot/1.0 (+catalogue image sync)"
TIMEOUT = 45
WORKERS = 6
# A polite gap between requests to the same host, in seconds. Suppliers are
# serving their own customers from these servers.
HOST_DELAY = 0.15
RETRIES = 2

_host_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
_host_last: dict[str, float] = defaultdict(float)
_write_lock = threading.Lock()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download catalogue images to local storage")
    parser.add_argument("--supplier", action="append", dest="suppliers", metavar="CODE")
    parser.add_argument("--limit", type=int, default=None, help="Stop after this many URLs")
    parser.add_argument("--out", type=Path, default=IMAGES_DIR, help="Where to keep the files")
    parser.add_argument("--workers", type=int, default=WORKERS)
    parser.add_argument("--max-side", type=int, default=MAX_SIDE, help="Longest side in pixels (0 = keep original)")
    parser.add_argument("--dry-run", action="store_true", help="Report what is left to fetch, download nothing")
    return parser.parse_args()


def encode_url(url: str) -> str:
    """Supplier URLs carry spaces and Cyrillic; both need escaping."""
    parts = urlsplit(url)
    path = quote(parts.path, safe="/%:@&=+$,~")
    query = quote(parts.query, safe="/?:@&=+$,~%")
    return f"{parts.scheme}://{parts.netloc}{path}" + (f"?{query}" if query else "")


def fetch(url: str) -> bytes:
    host = urlsplit(url).netloc
    for attempt in range(RETRIES + 1):
        with _host_locks[host]:
            wait = HOST_DELAY - (time.monotonic() - _host_last[host])
            if wait > 0:
                time.sleep(wait)
            _host_last[host] = time.monotonic()
        try:
            request = urllib.request.Request(encode_url(url), headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 403, 410) or attempt == RETRIES:
                raise
        except Exception:
            if attempt == RETRIES:
                raise
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("unreachable")


def store(data: bytes, out_dir: Path, max_side: int) -> tuple[str, str, int, int, int]:
    """Save the image, resized if need be. Returns (path, hash, w, h, bytes)."""
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError(f"not an image: {exc}") from exc

    width, height = image.size
    transparent = image.mode in ("RGBA", "LA", "P") and "transparency" in image.info or image.mode in ("RGBA", "LA")
    if max_side and max(width, height) > max_side:
        image.thumbnail((max_side, max_side), Image.LANCZOS)

    buffer = io.BytesIO()
    if transparent:
        image.convert("RGBA").save(buffer, format="PNG", optimize=True)
        suffix = "png"
    else:
        image.convert("RGB").save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
        suffix = "jpg"
    payload = buffer.getvalue()

    digest = hashlib.sha256(payload).hexdigest()
    target = out_dir / digest[:2] / f"{digest}.{suffix}"
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".part")
        tmp.write_bytes(payload)
        tmp.replace(target)  # never leave a half-written file behind
    return str(target), digest, image.size[0], image.size[1], len(payload)


PENDING_SQL = """
    SELECT pi.source_path, COUNT(*) AS rows_using
    FROM product_images pi
    JOIN suppliers s ON s.id = pi.supplier_id
    WHERE pi.stored_path IS NULL
      AND pi.source_type = 'url'
      AND pi.source_path LIKE 'http%%'
      {supplier_filter}
    GROUP BY pi.source_path
    ORDER BY rows_using DESC
    {limit}
"""

UPDATE_SQL = """
    UPDATE product_images
    SET stored_path = %s, file_hash = %s, width = %s, height = %s, file_size = %s
    WHERE source_path = %s AND stored_path IS NULL
"""

FAILED_SQL = """
    UPDATE product_images SET quality_note = %s
    WHERE source_path = %s AND stored_path IS NULL
"""


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
        sys.stderr.reconfigure(encoding="utf-8")

    args = parse_args()
    params: list[str] = []
    supplier_filter = ""
    if args.suppliers:
        placeholders = ", ".join(["%s"] * len(args.suppliers))
        supplier_filter = f"AND s.code IN ({placeholders})"
        params.extend(args.suppliers)
    sql = PENDING_SQL.format(
        supplier_filter=supplier_filter,
        limit=f"LIMIT {int(args.limit)}" if args.limit else "",
    )

    with db_session() as conn:
        pending = fetch_all(conn, sql, params)
        totals = fetch_one(
            conn,
            """
            SELECT COUNT(*) AS rows_total,
                   SUM(stored_path IS NOT NULL) AS done_rows,
                   COUNT(DISTINCT CASE WHEN stored_path IS NULL THEN source_path END) AS urls_left
            FROM product_images WHERE source_type = 'url'
            """,
        )
    print(
        f"Картинок в каталоге: {totals['rows_total']:,} | уже скачано строк: {int(totals['done_rows'] or 0):,} | "
        f"осталось адресов: {int(totals['urls_left'] or 0):,}"
    )
    print(f"В этом запуске: {len(pending):,} адресов -> {args.out}")
    if args.dry_run or not pending:
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    done = failed = skipped_rows = 0
    bytes_written = 0
    started = time.time()

    def handle(record: dict) -> tuple[str, tuple | None, str | None]:
        url = record["source_path"]
        try:
            data = fetch(url)
            return url, store(data, args.out, args.max_side), None
        except Exception as exc:
            return url, None, f"{type(exc).__name__}: {str(exc)[:120]}"

    with db_session() as conn, ThreadPoolExecutor(max_workers=args.workers) as pool:
        for index, (url, stored, error) in enumerate(pool.map(handle, pending), start=1):
            with _write_lock, conn.cursor() as cur:
                if stored:
                    path, digest, width, height, size = stored
                    cur.execute(UPDATE_SQL, (path, digest, width, height, size, url))
                    done += 1
                    bytes_written += size
                    skipped_rows += cur.rowcount
                else:
                    cur.execute(FAILED_SQL, (error, url))
                    failed += 1
            if index % 200 == 0:
                conn.commit()
                rate = index / max(time.time() - started, 1)
                print(
                    f"  {index:,}/{len(pending):,} | ок {done:,} | ошибок {failed:,} | "
                    f"{bytes_written / 1048576:,.0f} МБ | {rate:.1f} адр/с"
                )
        conn.commit()

    elapsed = time.time() - started
    print(
        f"Готово: {done:,} файлов ({bytes_written / 1073741824:.2f} ГБ), строк обновлено {skipped_rows:,}, "
        f"ошибок {failed:,}, за {elapsed / 60:.1f} мин"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
