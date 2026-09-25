#!/usr/bin/env python
"""Import Maytoni's own price list, photographs included.

    python scripts/import_maytoni.py --dry-run
    python scripts/import_maytoni.py
    python scripts/import_maytoni.py --refresh    # re-download the files first

The seven files live at https://shared.maytoni.ru/files/PRICE_RRC/ and are
public, so --refresh fetches them before importing.

Each product's photograph is embedded in the workbook. It is written out under
`MAYTONI_IMAGES_DIR`, named by the hash of its contents so a re-import produces
the same paths and a photo shared by two articles is stored once.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import pymysql

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from staging.db import db_session, fetch_one
from staging.importers.common import (
    ImportStats,
    finish_import_run,
    flush_product_batch,
    get_supplier_and_source,
    start_import_run,
)
from staging.importers.maytoni import PRICE_DIR, PRICE_URL, SOURCE_CODE, SUPPLIER_CODE, iter_maytoni_rows

IMAGES_DIR = Path(r"D:\projects\1C\Майтони\Фото из прайса")
BATCH_SIZE = 500
FILES = [
    "РРЦ_Freya_2026 NEW.xlsx", "РРЦ_Ledstrip_2026 NEW.xlsx",
    "РРЦ_Lighting_control_2026 NEW.xlsx", "РРЦ_Maytoni_2026 NEW.xlsx",
    "РРЦ_Outdoor_2026 NEW.xlsx", "РРЦ_Technical_2026 NEW.xlsx",
    "РРЦ_Voltega_2026 NEW.xlsx",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import the Maytoni price list")
    parser.add_argument("--dir", type=Path, default=PRICE_DIR, help="Where the price files are")
    parser.add_argument("--images", type=Path, default=IMAGES_DIR, help="Where to write the photographs")
    parser.add_argument("--dry-run", action="store_true", help="Parse and report, write nothing")
    parser.add_argument("--refresh", action="store_true", help="Download the files again first")
    parser.add_argument("--limit", type=int, default=None)
    return parser.parse_args()


def download(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        url = PRICE_URL + urllib.parse.quote(name)
        request = urllib.request.Request(url, headers={"User-Agent": "SvetoyarStagingBot/1.0"})
        with urllib.request.urlopen(request, timeout=300) as response:
            payload = response.read()
        (directory / name).write_bytes(payload)
        print(f"  скачан {name} ({len(payload) / 1048576:.1f} МБ)")


def save_photo(image, out_dir: Path) -> str | None:
    if image is None:
        return None
    digest = hashlib.sha1(image.data).hexdigest()[:16]
    suffix = "jpg" if image.extension in ("jpeg", "jpg") else image.extension
    target = out_dir / digest[:2] / f"{digest}.{suffix}"
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(image.data)
    return str(target)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
        sys.stderr.reconfigure(encoding="utf-8")

    args = parse_args()
    if args.refresh:
        print(f"Загрузка прайсов из {PRICE_URL}")
        download(args.dir)

    try:
        if args.dry_run:
            total = photos = with_barcode = 0
            brands: dict[str, int] = {}
            for item in iter_maytoni_rows(args.dir, progress=print):
                total += 1
                photos += 1 if item["_image"] else 0
                with_barcode += 1 if item["barcode"] else 0
                brands[item["brand"] or "(без бренда)"] = brands.get(item["brand"] or "(без бренда)", 0) + 1
                if args.limit and total >= args.limit:
                    break
            print(f"\nВсего товаров: {total:,} | с фото: {photos:,} | со штрихкодом: {with_barcode:,}")
            for brand, count in sorted(brands.items(), key=lambda kv: -kv[1]):
                print(f"   {brand:<20} {count:>6,}")
            print("\nDry run - ничего не записано.")
            return 0

        with db_session() as conn:
            supplier_id, source_id = get_supplier_and_source(conn, SUPPLIER_CODE, SOURCE_CODE)
            # The seven files together are the source, so the run is stamped
            # with a hash over all of them: a re-run on unchanged files is
            # visibly the same source.
            digest = hashlib.sha256()
            for path in sorted(args.dir.glob("*.xlsx")):
                if not path.name.startswith("~$"):
                    digest.update(path.name.encode("utf-8"))
                    digest.update(str(path.stat().st_size).encode("utf-8"))
            run_id = start_import_run(
                conn, supplier_id, source_id, f"{PRICE_URL} ({len(FILES)} файлов)", digest.hexdigest()
            )
            stats = ImportStats()
            existing: dict[str, str] = {}  # filled per batch by flush_product_batch
            batch: list[dict] = []
            photos = 0

            try:
                for item in iter_maytoni_rows(args.dir, progress=print):
                    stats.rows_total += 1
                    stored = save_photo(item.pop("_image", None), args.images)
                    item.pop("_source_file", None)
                    if stored:
                        item["images_json"] = [stored]
                        photos += 1
                    batch.append(item)
                    if len(batch) >= BATCH_SIZE:
                        flush_product_batch(conn, supplier_id, run_id, batch, existing, stats)
                    if args.limit and stats.rows_total >= args.limit:
                        break
                flush_product_batch(conn, supplier_id, run_id, batch, existing, stats)
                finish_import_run(conn, run_id, stats, status="success")
            except Exception:
                conn.rollback()
                finish_import_run(conn, run_id, stats, status="failed")
                raise

            total = fetch_one(
                conn,
                "SELECT COUNT(*) AS cnt FROM supplier_products WHERE supplier_id = %s",
                (supplier_id,),
            )

        print("\nИмпорт завершён.")
        print(f"  Прочитано:  {stats.rows_total:,}")
        print(f"  Новых:      {stats.rows_imported:,}")
        print(f"  Обновлено:  {stats.rows_updated:,}")
        print(f"  Без изменений: {stats.rows_skipped:,}")
        print(f"  Ошибок:     {stats.rows_errors:,}")
        print(f"  Фотографий сохранено: {photos:,} -> {args.images}")
        print(f"  Всего в базе Maytoni: {total['cnt'] if total else 0:,}")
        return 0
    except pymysql.err.OperationalError as exc:
        print("Не удалось подключиться к MySQL.", file=sys.stderr)
        print(f"  {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
