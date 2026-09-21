"""Conformity documents and the products they cover.

The site needs a registry link on each product page - a declaration or
certificate that can be checked in the official register. Three suppliers
publish registries, and they bind documents to products in two ways:

* Arlight and Jazzway list every document against explicit article numbers.
  Those are loaded as they are.
* LED Crystal and Salux publish documents by series, in prose: "серии LB, LT",
  "ССдВз 1Ex 01; ССдВз 1Ex 02 db". There are only 17 such documents, so they are
  matched through the rule table below rather than by parsing the prose. A table
  of rules can be read and corrected; a parser of sentences cannot. Salux's own
  registry warns that binding by series needs an article-to-series table from the
  supplier, so every series link is marked as such and can be audited.

Dekomo, SWG and ViaSvet publish nothing usable, so their products get no
documents here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

import pandas as pd
from pymysql.connections import Connection

from staging.db import fetch_all, fetch_one
from staging.importers.jazzway_feed import order_code_for
from staging.pricing import fold

ARLIGHT_REGISTRY = r"D:\projects\1C\Арлайт\Reestr_dokumentov_sootvetstviya_Svetoyar.xlsx"
CRYSTAL_REGISTRY = r"D:\projects\1C\crystal\Реестр_сертификатов_LED_CRYSTAL(2).xlsx"
SALUX_REGISTRY = r"D:\projects\1C\Салюкс\Реестр_сертификатов_Салюкс_обновлён.xlsx"
JAZZWAY_REGISTRY = r"D:\projects\1C\Джазвея\Джаз-Вей - Сертификаты номенклатуры.xlsx"

# nsopb.ru is the register of the voluntary fire-safety certification system, and
# nsi.eaeunion.org the EAEU register that holds state registration certificates.
# Both are the issuing body's own register, which is what a customer needs to be
# able to check - they are just not the FSA one.
OFFICIAL_REGISTRY_HOSTS = ("pub.fsa.gov.ru", "rivreg.ru", "nsopb.ru", "nsi.eaeunion.org")
LINK_INSERT_CHUNK = 2000


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


@dataclass
class Certificate:
    code: str
    doc_kind: str
    doc_kind_label: str | None
    doc_number: str | None
    scope_text: str | None
    regulation: str | None
    valid_from: date | None
    valid_to: date | None
    registry_url: str | None
    other_url: str | None
    scan_url: str | None
    link_status: str
    is_product_document: bool = True
    source: str | None = None
    notes: str | None = None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    text = " ".join(str(value).replace("&#13;", " ").split())
    return None if text in ("", "—", "-", "nan", "NaT") else text


def _date(value: Any) -> date | None:
    if value is None or (isinstance(value, float) and pd.isna(value)) or value is pd.NaT:
        return None
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.date()
    if isinstance(value, date):
        return value
    match = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", str(value))
    if not match:
        return None  # "Не установлен" and friends: no end date
    day, month, year = (int(part) for part in match.groups())
    return date(year, month, day)


def _row_number(value: Any) -> int | None:
    """The "№" of a registry row. pandas can hand it back as 1, 1.0 or "1"; a
    note row that spans the table gives text, which is not a document."""
    text = _text(value)
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return int(number) if number.is_integer() else None


def _kind(label: str | None) -> str:
    """Classify a document from its type label. Order matters: a voluntary
    certificate and an ISO certificate both also say "сертификат"."""
    text = (label or "").lower()
    if "iso" in text:
        return "quality_system"
    if "отказн" in text:
        return "refusal_letter"
    # Jazzway's labels. ДСС is a voluntary certificate issued by a private
    # system - fire safety, emergency lighting, schools. СГР is a state
    # registration certificate, which approves rather than certifies.
    if text.startswith("дсс"):
        return "voluntary"
    if text == "сгр":
        return "approval"
    if "добровол" in text:
        return "voluntary"
    if "декларац" in text:
        return "declaration"
    if "свидетельств" in text or "одобрен" in text:
        return "approval"
    if "сертификат" in text:
        return "certificate"
    return "other"


def _is_official(url: str | None) -> bool:
    return bool(url) and any(host in url for host in OFFICIAL_REGISTRY_HOSTS)


def _header_row(frame: pd.DataFrame, label: str) -> int:
    for index in range(len(frame)):
        cells = [_text(cell) for cell in frame.iloc[index].tolist()]
        if label in cells:
            return index
    raise ValueError(f"Header row with {label!r} not found")


def _records(path: str, sheet: str, header_label: str) -> list[dict[str, Any]]:
    """Rows of a registry sheet keyed by header, header text whitespace-folded."""
    frame = pd.read_excel(path, sheet_name=sheet, header=None, engine="openpyxl")
    header_index = _header_row(frame, header_label)
    headers = [_text(cell) or f"col{i}" for i, cell in enumerate(frame.iloc[header_index])]
    rows = []
    for index in range(header_index + 1, len(frame)):
        values = frame.iloc[index].tolist()
        rows.append(dict(zip(headers, values)))
    return rows


# --- Arlight ---------------------------------------------------------------

ARLIGHT_LINK_STATUS = {
    "Ссылка подтверждена — официальный реестр ФСА": "official",
    "Ссылка подтверждена — официальный реестр Кыргызстана": "official",
    "Не требуется — отказное письмо": "not_required",
    "Ссылка на документ — неофициальный ресурс": "unofficial",
    "Не удалось подтвердить": "unconfirmed",
    "Уточнить": "unconfirmed",
    "В ожидании документа": "pending",
}


def load_arlight(path: str = ARLIGHT_REGISTRY) -> tuple[list[Certificate], list[tuple[str, str]]]:
    """Documents, and (document code, article) links."""
    certificates = []
    for row in _records(path, "Реестр документов", "ID документа"):
        code = _text(row.get("ID документа"))
        if not code:
            continue
        number = _text(row.get("Номер документа"))
        status = ARLIGHT_LINK_STATUS.get(_text(row.get("Статус ссылки на реестр")) or "", "unconfirmed")
        if number and number.lower() == "в ожидании":
            status = "pending"  # a placeholder row, not a document yet
        url = _text(row.get("Ссылка на реестр / документ"))
        label = _text(row.get("Вид документа"))
        certificates.append(
            Certificate(
                code=code,
                doc_kind=_kind(label),
                doc_kind_label=label,
                doc_number=number,
                scope_text=_text(row.get("Описание / область действия")),
                regulation=None,
                valid_from=_date(row.get("Дата регистрации")),
                valid_to=_date(row.get("Действует до")),
                # Arlight's own status column says which links are the official
                # register, so it is trusted over a guess from the domain.
                registry_url=url if status == "official" else None,
                other_url=url if status != "official" else None,
                scan_url=_text(row.get("Ссылка на PDF / скан")),
                link_status=status,
                source=_text(row.get("Источник")),
                notes=_text(row.get("Комментарий")),
            )
        )

    links = pd.read_excel(path, sheet_name="Привязка к товарам", header=3, dtype=str, engine="openpyxl")
    links.columns = [str(column).strip() for column in links.columns]
    pairs = [
        (code.strip(), sku.strip())
        for code, sku in zip(links["ID документа"], links["Артикул поставщика"])
        if isinstance(code, str) and isinstance(sku, str) and code.strip() and sku.strip()
    ]
    return certificates, pairs


# --- Jazzway ---------------------------------------------------------------

JAZZWAY_SHEET = "TDSheet"
JAZZWAY_HEADER = "Артикул (РМ)"


def load_jazzway(path: str = JAZZWAY_REGISTRY) -> tuple[list[Certificate], list[tuple[str, str]]]:
    """Documents, and (document code, article) links.

    The file is a 1C report: one row per article and document, the article
    written once and left blank on the rows that follow it. The same document
    therefore appears on hundreds of rows; it is collapsed here by its number,
    which the registry spells consistently - no document number carries two
    different types or dates.

    Documents have no code of their own, so they are numbered JAZ-001 upwards in
    order of document number. Sorting keeps the numbering the same between runs
    as long as the set of documents is.
    """
    frame = pd.read_excel(path, sheet_name=JAZZWAY_SHEET, header=None, engine="openpyxl")
    header_index = _header_row(frame, JAZZWAY_HEADER)
    headers = [_text(cell) or f"col{i}" for i, cell in enumerate(frame.iloc[header_index])]

    rows: list[dict[str, Any]] = []
    article: str | None = None
    for index in range(header_index + 1, len(frame)):
        row = dict(zip(headers, frame.iloc[index].tolist()))
        article = _text(row.get(JAZZWAY_HEADER)) or article
        number = _text(row.get("Сертификат.Номер"))
        if article and number:  # an article with no document at all is just skipped
            rows.append({**row, "article": article, "number": number})

    codes: dict[str, str] = {
        number: f"JAZ-{position:03d}"
        for position, number in enumerate(sorted({row["number"] for row in rows}), start=1)
    }

    certificates: list[Certificate] = []
    seen: set[str] = set()
    pairs: list[tuple[str, str]] = []
    linked: set[tuple[str, str]] = set()
    for row in rows:
        number = row["number"]
        code = codes[number]
        if number not in seen:
            seen.add(number)
            url = _text(row.get("Ссылка на ФСА"))
            label = _text(row.get("Сертификат.Тип сертификата"))
            kind = _kind(label)
            official = _is_official(url)
            certificates.append(
                Certificate(
                    code=code,
                    doc_kind=kind,
                    doc_kind_label=label,
                    doc_number=number,
                    scope_text=None,  # the report names the articles, not the scope
                    regulation=None,
                    valid_from=_date(row.get("Сертификат.Дата начала срока действия")),
                    valid_to=_date(row.get("Сертификат.Дата окончания срока действия")),
                    registry_url=url if official else None,
                    other_url=url if url and not official else None,
                    scan_url=None,
                    link_status=(
                        "official" if official
                        # A refusal letter states the goods need no certificate,
                        # so there is no register for it to be in.
                        else "not_required" if kind == "refusal_letter"
                        else "unconfirmed"
                    ),
                    source="Выгрузка «Сертификаты номенклатуры» из 1С Джаз-Вей, 27.08.2026",
                )
            )
        if (code, row["article"]) not in linked:
            linked.add((code, row["article"]))
            pairs.append((code, row["article"]))

    return certificates, pairs


# --- LED Crystal -----------------------------------------------------------

# The registry lists the power supply certificate as expired on 19.05.2026. The
# client then sent its replacement as a PDF, which no registry file carries yet.
CRYSTAL_REPLACEMENT = Certificate(
    code="CRY-08",
    doc_kind="declaration",
    doc_kind_label="Декларация о соответствии (на партию)",
    doc_number="ВП RU Д-CN.РА01.А.16784/26",
    scope_text=(
        "Источники питания для светодиодной продукции, серии (типы) LB, LT; ТМ LED CRYSTAL. "
        "Партия 165 000 шт., инвойс 67SNT3011 от 15.04.2026."
    ),
    regulation="ТР ТС 004/2011; ТР ТС 020/2011",
    valid_from=date(2026, 4, 30),
    valid_to=date(2026, 10, 22),
    registry_url="https://pub.fsa.gov.ru/rds/declaration/view/21359081/common",
    other_url=None,
    scan_url=None,
    link_status="official",
    source="PDF от клиента, 16.09.2026",
    notes=(
        "Заменяет истёкший сертификат ЕАЭС RU C-CN.НВ65.В.01216/21 (CRY-02). "
        "Декларация на партию: действует для конкретной поставки, а не для производства в целом."
    ),
)


def load_crystal(path: str = CRYSTAL_REGISTRY) -> list[Certificate]:
    certificates = []
    for row in _records(path, "Сертификаты", "Номер документа"):
        number = _row_number(row.get("№"))
        if number is None:
            continue  # the trailing "Важно:" note spans the whole row
        registry = _text(row.get("Реестр / QR"))
        label = _text(row.get("Тип документа"))
        certificates.append(
            Certificate(
                code=f"CRY-{number:02d}",
                doc_kind=_kind(label),
                doc_kind_label=label,
                doc_number=_text(row.get("Номер документа")),
                scope_text=_text(row.get("Продукция / серии")),
                regulation=_text(row.get("ТР / стандарт")),
                valid_from=_date(row.get("Дата начала / регистрации")),
                valid_to=_date(row.get("Дата окончания")),
                registry_url=registry if _is_official(registry) else None,
                other_url=registry if registry and not _is_official(registry) else None,
                scan_url=_text(row.get("Скан на сайте LED CRYSTAL")),
                link_status="official" if _is_official(registry) else "unconfirmed",
                source="Реестр LED CRYSTAL, проверка 03.09.2026",
                notes=_text(row.get("Проверка и примечание")),
            )
        )
    certificates.append(CRYSTAL_REPLACEMENT)
    return certificates


# --- Salux -----------------------------------------------------------------

# Rows 2 and 4 of the registry are the same RKO approval, published as two files.
# The link on row 2 says ССдВз while the scan itself says ССдС - the registry flags
# it for the supplier. It is loaded once, scoped to ССдС as the scan reads.
SALUX_DUPLICATE_OF = {2: 4}


def load_salux(path: str = SALUX_REGISTRY) -> list[Certificate]:
    certificates = []
    for row in _records(path, "Реестр документов", "Номер"):
        number = _row_number(row.get("№"))
        if number is None or number in SALUX_DUPLICATE_OF:
            continue
        registry = _text(row.get("Реестр"))
        label = _text(row.get("Документ"))
        kind = _kind(label)
        notes = _text(row.get("Примечание"))
        if number == 4:
            notes = (
                "На сайте поставщика опубликован двумя файлами (строки 2 и 4 реестра). "
                "В подписи одной ссылки указана серия ССдВз, но в самом скане — ССдС; "
                "расхождение нужно уточнить у поставщика. " + (notes or "")
            ).strip()
        certificates.append(
            Certificate(
                code=f"SAL-{number:02d}",
                doc_kind=kind,
                doc_kind_label=label,
                doc_number=_text(row.get("Номер")),
                scope_text=_text(row.get("Серии / продукция")),
                regulation=_text(row.get("Регламент / назначение")),
                valid_from=_date(row.get("Действует с")),
                valid_to=_date(row.get("Действует по")),
                registry_url=registry if _is_official(registry) else None,
                other_url=registry if registry and not _is_official(registry) else None,
                scan_url=_text(row.get("Файл на сайте")),
                link_status="official" if _is_official(registry) else "unconfirmed",
                # ISO 9001 certifies the company's quality system, not a product.
                is_product_document=kind != "quality_system",
                source="Реестр СТЗ «САЛЮКС», раздел «Документы» на сайте",
                notes=notes,
            )
        )
    return certificates


# ---------------------------------------------------------------------------
# Series rules
# ---------------------------------------------------------------------------
#
# Everything is compared through `fold`, which lowercases and turns Latin
# lookalikes into Cyrillic. The Salux documents write "1Ex" in Latin while the
# price list markings use Cyrillic "1Ех", and LED Crystal writes its luminaire
# prefix as both "LC" and "LС" with a Cyrillic С.


@dataclass(frozen=True)
class ScopeRule:
    """How an LED Crystal document reaches a product: by article prefix, the price
    list sheet, and where needed the product name."""

    code: str
    sku_prefix: str = ""
    category_contains: str | None = None
    sku_contains: str | None = None
    sku_excludes: str | None = None
    name_pattern: str | None = None  # a regex, matched against the folded name

    def matches(self, product: dict[str, Any]) -> bool:
        sku = fold(product["supplier_sku"])
        if self.sku_prefix and not sku.startswith(fold(self.sku_prefix)):
            return False
        category = fold(product.get("supplier_category"))
        if self.category_contains and fold(self.category_contains) not in category:
            return False
        if self.sku_contains and fold(self.sku_contains) not in sku:
            return False
        if self.sku_excludes and fold(self.sku_excludes) in sku:
            return False
        if self.name_pattern and not re.search(self.name_pattern, fold(product.get("name"))):
            return False
        return True

    def describe(self) -> str:
        parts = []
        if self.sku_prefix:
            parts.append(f"артикул начинается с «{self.sku_prefix}»")
        if self.category_contains:
            parts.append(f"раздел содержит «{self.category_contains}»")
        if self.sku_contains:
            parts.append(f"артикул содержит «{self.sku_contains}»")
        if self.sku_excludes:
            parts.append(f"артикул без «{self.sku_excludes}»")
        if self.name_pattern:
            parts.append("название указывает профиль серии L")
        return "; ".join(parts)[:255]


# A Salux marking is "<family> <series>-<nnn>-<nnn> ...". The supplier's own price
# list breaks that shape three ways, all absorbed here rather than by extra rules:
# a space where the dash belongs ("ССдВз 1Ех 02 010-010", three Оникс
# luminaires), "Ех" written twice ("ССдВз 1Ех Ех 02", two Гранит), and the db and
# db E27 variants of series 02. Matched against the folded marking, so "db" reads
# as "dв" and "E27" as "е27". Longer family names come first in the alternation so
# ССдПб is not read as ССдП.
_SALUX_MARKING = re.compile(
    r"^(?P<family>ссдвз 1ех|ссдвз ех|ссдпб|ссдп|ссду|ссдс|ссдо|ксду)"
    r"(?: ех)?"
    r" (?P<series>\d{2}(?: dв(?: е27)?)?)"
    r"[ -]\d"
)


def parse_salux_marking(sku: str) -> tuple[str, str] | None:
    """(family, series) from a Salux marking, both folded, or None."""
    match = _SALUX_MARKING.match(fold(sku))
    return (match.group("family"), match.group("series")) if match else None


@dataclass(frozen=True)
class SeriesRule:
    """A Salux document naming a family and series, e.g. ССдВз 1Ex 02 db."""

    code: str
    family: str
    series: str

    def matches(self, product: dict[str, Any]) -> bool:
        return parse_salux_marking(product["supplier_sku"]) == (fold(self.family), fold(self.series))

    def describe(self) -> str:
        return f"серия {self.family} {self.series}"


def _series(code: str, family: str, *series: str) -> list[SeriesRule]:
    return [SeriesRule(code, family, one) for one in series]


# The broad Salux declarations name families as "серии 01–03". The family written
# plainly as "ССдВз" is read as the non-1Ex explosion-proof line, which the price
# list calls "ССдВз Ех". 02 db and 02 db E27 are variants of series 02, so a
# document covering a whole family's series 02 covers them; the narrow approvals
# name the variants they cover explicitly.
_SALUX_BROAD_FAMILIES = ("ССдП", "ССдУ", "ССдПб", "ССдВз 1Ех", "ССдВз Ех", "ССдС", "ССдО")


def _salux_broad(code: str) -> list[SeriesRule]:
    rules = []
    for family in _SALUX_BROAD_FAMILIES:
        rules += _series(code, family, "01", "02", "03")
    rules += _series(code, "ССдВз 1Ех", "02 db", "02 db E27")
    return rules


SCOPE_RULES: dict[str, list[Any]] = {
    "crystal": [
        # Tape and profile share the LR prefix; the price list sheet separates them.
        ScopeRule("CRY-01", "LR", category_contains="светодиодные лент"),
        ScopeRule("CRY-03", "L", category_contains="профил"),
        # Profile components - connectors, end caps, holders. Their articles start
        # with L, LR or R, but every one names the L-series profile it fits, and the
        # document covers "профиль ... и комплектующие; модель L***".
        ScopeRule("CRY-03", category_contains="аксессуар", name_pattern=r"\blr?\d"),
        # Power supplies. LB also prefixes lamps on another sheet, which no
        # document covers, so the sheet is required.
        ScopeRule("CRY-02", "LB", category_contains="источники питания"),
        ScopeRule("CRY-02", "LT", category_contains="источники питания"),
        ScopeRule("CRY-08", "LB", category_contains="источники питания"),
        ScopeRule("CRY-08", "LT", category_contains="источники питания"),
        # LC is luminaires on one sheet and controllers on another; the HV models
        # have their own certificate.
        ScopeRule("CRY-04", "LC", category_contains="светильник", sku_excludes="HV"),
        ScopeRule("CRY-05", "LC", category_contains="светильник", sku_contains="HV"),
        ScopeRule("CRY-06", "LC", category_contains="управлени"),
        ScopeRule("CRY-07", "LS"),
    ],
    "salux": [
        *_series("SAL-01", "ССдВз 1Ех", "01", "02 db", "03"),
        *_series("SAL-04", "ССдС", "01", "02", "03"),
        *_series("SAL-03", "ССдС", "01", "03"),
        *_salux_broad("SAL-05"),
        *_salux_broad("SAL-06"),
        *_series("SAL-08", "ССдПб", "01", "02", "03"),
        *_series("SAL-08", "ССдВз 1Ех", "01", "02", "03", "02 db", "02 db E27"),
        *_series("SAL-08", "ССдВз Ех", "01", "02", "03"),
        *_series("SAL-09", "ССдО", "01", "02", "03"),
        *_series("SAL-10", "ССдВз 1Ех", "01", "02", "03", "02 db", "02 db E27"),
        *_series("SAL-10", "ССдВз Ех", "01", "02", "03"),
    ],
}


def bind_series(rules: list[Any], products: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    """(document code, article, rule description) for every rule that matches."""
    links = []
    seen: set[tuple[str, str]] = set()
    for product in products:
        sku = product["supplier_sku"]
        for rule in rules:
            if (rule.code, sku) in seen:
                continue
            if rule.matches(product):
                seen.add((rule.code, sku))
                links.append((rule.code, sku, rule.describe()))
    return links


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


CERTIFICATE_UPSERT = """
    INSERT INTO certificates (
        supplier_id, code, doc_kind, doc_kind_label, doc_number, scope_text, regulation,
        valid_from, valid_to, registry_url, other_url, scan_url, link_status,
        is_product_document, source, notes
    ) VALUES (
        %(supplier_id)s, %(code)s, %(doc_kind)s, %(doc_kind_label)s, %(doc_number)s,
        %(scope_text)s, %(regulation)s, %(valid_from)s, %(valid_to)s, %(registry_url)s,
        %(other_url)s, %(scan_url)s, %(link_status)s, %(is_product_document)s,
        %(source)s, %(notes)s
    )
    ON DUPLICATE KEY UPDATE
        doc_kind = VALUES(doc_kind), doc_kind_label = VALUES(doc_kind_label),
        doc_number = VALUES(doc_number), scope_text = VALUES(scope_text),
        regulation = VALUES(regulation), valid_from = VALUES(valid_from),
        valid_to = VALUES(valid_to), registry_url = VALUES(registry_url),
        other_url = VALUES(other_url), scan_url = VALUES(scan_url),
        link_status = VALUES(link_status), is_product_document = VALUES(is_product_document),
        source = VALUES(source), notes = VALUES(notes)
"""

LINK_INSERT = """
    INSERT INTO product_certificates (certificate_id, supplier_id, supplier_sku, match_type, match_basis)
    VALUES (%s, %s, %s, %s, %s)
"""


@dataclass
class LoadResult:
    supplier: str
    documents: int = 0
    links: int = 0
    missing_codes: set[str] = field(default_factory=set)


def _supplier_id(conn: Connection, code: str) -> int:
    row = fetch_one(conn, "SELECT id FROM suppliers WHERE code = %s", (code,))
    if not row:
        raise RuntimeError(f"Supplier '{code}' not found")
    return int(row["id"])


def save(
    conn: Connection,
    supplier_code: str,
    certificates: list[Certificate],
    links: list[tuple[str, str, str, str | None]],
) -> LoadResult:
    """Replace one supplier's documents and links. Safe to re-run.

    links are (document code, article, match_type, match_basis).
    """
    supplier_id = _supplier_id(conn, supplier_code)
    result = LoadResult(supplier_code, documents=len(certificates))

    # A registry that parsed to nothing is a broken file, not an instruction to
    # delete every document we hold for that supplier.
    if not certificates:
        raise ValueError(
            f"Registry for '{supplier_code}' yielded no documents - refusing to wipe the existing ones"
        )

    with conn.cursor() as cur:
        for cert in certificates:
            cur.execute(
                CERTIFICATE_UPSERT,
                {**cert.__dict__, "supplier_id": supplier_id,
                 "is_product_document": 1 if cert.is_product_document else 0},
            )
        codes = [cert.code for cert in certificates]
        placeholders = ", ".join(["%s"] * len(codes))
        # A document dropped from the registry goes too, and its links with it.
        cur.execute(
            f"DELETE FROM certificates WHERE supplier_id = %s AND code NOT IN ({placeholders})",
            [supplier_id, *codes],
        )
        cur.execute("DELETE FROM product_certificates WHERE supplier_id = %s", (supplier_id,))

    ids = {
        row["code"]: row["id"]
        for row in fetch_all(conn, "SELECT id, code FROM certificates WHERE supplier_id = %s", (supplier_id,))
    }
    rows = []
    for code, sku, match_type, basis in links:
        if code not in ids:
            result.missing_codes.add(code)
            continue
        rows.append((ids[code], supplier_id, sku[:128], match_type, basis))
    with conn.cursor() as cur:
        for start in range(0, len(rows), LINK_INSERT_CHUNK):
            cur.executemany(LINK_INSERT, rows[start : start + LINK_INSERT_CHUNK])
    conn.commit()
    result.links = len(rows)
    return result


def supplier_products(conn: Connection, supplier_code: str) -> list[dict[str, Any]]:
    return fetch_all(
        conn,
        """
        SELECT sp.supplier_sku, sp.supplier_category, sp.name
        FROM supplier_products sp JOIN suppliers s ON s.id = sp.supplier_id
        WHERE s.code = %s
        """,
        (supplier_code,),
    )


def jazzway_links(
    conn: Connection, pairs: list[tuple[str, str]]
) -> list[tuple[str, str, str, str | None]]:
    """Registry links for the articles we actually carry.

    The registry writes the article bare, the price list with a leading dot
    (".5027244"), and the link has to carry the price list's spelling - that is
    what `product_certificates` joins on. Articles the registry covers but our
    price list does not are dropped.
    """
    index = {
        order_code_for(product["supplier_sku"]): product["supplier_sku"]
        for product in supplier_products(conn, "jazzway")
    }
    return [(code, index[article], "explicit", None) for code, article in pairs if article in index]


def load_all(conn: Connection, progress: Callable[[str], None] = print) -> list[LoadResult]:
    results = []

    certs, pairs = load_arlight()
    progress(f"Арлайт: {len(certs)} документов, {len(pairs):,} связей с артикулами")
    results.append(save(conn, "arlight", certs, [(c, s, "explicit", None) for c, s in pairs]))

    certs, pairs = load_jazzway()
    links = jazzway_links(conn, pairs)
    progress(f"Jazzway: {len(certs)} документов, {len(links):,} связей с артикулами")
    results.append(save(conn, "jazzway", certs, links))

    for supplier, loader in (("crystal", load_crystal), ("salux", load_salux)):
        certs = loader()
        products = supplier_products(conn, supplier)
        links = bind_series(SCOPE_RULES[supplier], products)
        progress(f"{supplier}: {len(certs)} документов, {len(links)} связей по сериям на {len(products)} товаров")
        results.append(save(conn, supplier, certs, [(c, s, "series", b) for c, s, b in links]))

    return results


def coverage(conn: Connection, today: date | None = None) -> list[dict[str, Any]]:
    """Per supplier, what each product can actually show a customer.

    A product lands in the first bucket its documents allow:

    * official - a product document, in date, with a link to the official
      register. This is what the client asked to put on the site.
    * scan_only - a product document in date with a scan but no register entry.
      Mostly refusal letters, which by design have none: they state the product is
      exempt from mandatory certification. Also voluntary certificates. Still a
      real document worth showing, just not as a register link.
    * only_expired - every document the product has is past its end date.
    * nothing_to_show - placeholders awaiting a document, or no link and no scan.

    An earlier version counted any product linked to an expired document, which
    flagged 51 LED Crystal power supplies whose expired certificate is already
    superseded by a valid declaration.
    """
    today = today or date.today()
    return fetch_all(
        conn,
        """
        SELECT totals.supplier,
               totals.products,
               COALESCE(docs.with_any, 0)        AS with_any,
               COALESCE(docs.official, 0)        AS official,
               COALESCE(docs.scan_only, 0)       AS scan_only,
               COALESCE(docs.only_expired, 0)    AS only_expired,
               COALESCE(docs.nothing_to_show, 0) AS nothing_to_show
        FROM (
            SELECT s.id AS supplier_id, s.code AS supplier, COUNT(*) AS products
            FROM suppliers s JOIN supplier_products sp ON sp.supplier_id = s.id
            GROUP BY s.id, s.code
        ) totals
        LEFT JOIN (
            SELECT supplier_id,
                   COUNT(*) AS with_any,
                   SUM(official_ok) AS official,
                   SUM(official_ok = 0 AND scan_ok = 1) AS scan_only,
                   SUM(official_ok = 0 AND scan_ok = 0 AND all_expired = 1) AS only_expired,
                   SUM(official_ok = 0 AND scan_ok = 0 AND all_expired = 0) AS nothing_to_show
            FROM (
                SELECT pc.supplier_id, pc.supplier_sku,
                       MAX(c.is_product_document = 1 AND c.link_status = 'official'
                           AND (c.valid_to IS NULL OR c.valid_to >= %s)) AS official_ok,
                       MAX(c.is_product_document = 1 AND c.link_status <> 'pending'
                           AND c.scan_url IS NOT NULL
                           AND (c.valid_to IS NULL OR c.valid_to >= %s)) AS scan_ok,
                       MIN(c.valid_to IS NOT NULL AND c.valid_to < %s) AS all_expired
                FROM product_certificates pc
                JOIN certificates c ON c.id = pc.certificate_id
                JOIN supplier_products sp
                  ON sp.supplier_id = pc.supplier_id AND sp.supplier_sku = pc.supplier_sku
                GROUP BY pc.supplier_id, pc.supplier_sku
            ) per_product
            GROUP BY supplier_id
        ) docs ON docs.supplier_id = totals.supplier_id
        ORDER BY totals.products DESC
        """,
        (today, today, today),
    )
