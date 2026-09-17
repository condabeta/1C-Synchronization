#!/usr/bin/env python
"""Load conformity documents and link them to products.

Reads the Arlight, LED Crystal and Salux registries. Arlight's links come
straight from its registry; LED Crystal and Salux documents are matched to
products by series through the rules in staging/certificates.py. Re-running is
safe: each supplier's documents and links are replaced as a whole.

    python scripts/import_certificates.py --dry-run
    python scripts/import_certificates.py
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

import pymysql

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from staging import certificates as C
from staging.db import db_session


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import conformity documents")
    parser.add_argument("--dry-run", action="store_true", help="Parse and match only, write nothing")
    return parser.parse_args()


def print_coverage(rows: list[dict]) -> None:
    print()
    print("Что товар может показать покупателю")
    print(f"  {'поставщик':<10}{'товаров':>9}{'с документом':>14}{'ссылка на реестр':>18}"
          f"{'только скан':>13}{'только истёкшие':>17}{'нечего показать':>17}")
    for row in rows:
        if not row["with_any"]:
            continue
        print(
            f"  {row['supplier']:<10}{row['products']:>9,}{row['with_any']:>14,}{row['official']:>18,}"
            f"{row['scan_only']:>13,}{row['only_expired']:>17,}{row['nothing_to_show']:>17,}"
        )
    print("  ссылка на реестр - товарный документ в сроке с официальной ссылкой (ФСА, РКО).")
    print("  только скан - документ в сроке без записи в реестре: отказные письма и добровольные сертификаты.")


def dry_run() -> int:
    certs, pairs = C.load_arlight()
    print(f"Арлайт: {len(certs)} документов, {len(pairs):,} связей с артикулами")
    print("   ", dict(Counter(c.link_status for c in certs)))
    with db_session() as conn:
        for supplier, loader in (("crystal", C.load_crystal), ("salux", C.load_salux)):
            certs = loader()
            products = C.supplier_products(conn, supplier)
            links = C.bind_series(C.SCOPE_RULES[supplier], products)
            bound = {sku for _, sku, _ in links}
            print(
                f"{supplier}: {len(certs)} документов | товаров {len(products)} | "
                f"привязано {len(bound)} | без документа {len(products) - len(bound)}"
            )
    print("\nDry run - ничего не записано.")
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
        sys.stderr.reconfigure(encoding="utf-8")

    args = parse_args()
    try:
        if args.dry_run:
            return dry_run()

        with db_session() as conn:
            results = C.load_all(conn)
            for result in results:
                line = f"  {result.supplier}: {result.documents} документов, {result.links:,} связей"
                if result.missing_codes:
                    line += f" | правила ссылаются на неизвестные коды: {sorted(result.missing_codes)}"
                print(line)
            print_coverage(C.coverage(conn))
        return 0
    except pymysql.err.OperationalError as exc:
        print("Не удалось подключиться к MySQL.", file=sys.stderr)
        print(f"  {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
