"""Conformity documents and the products they cover.

The site needs a registry link on each product page - a declaration or
certificate that can be checked in the official register. Five suppliers give us
something to work from, and they bind documents to products in two ways:

* Arlight, Jazzway and Dekomo name explicit article numbers. Those are loaded as
  they are.
* LED Crystal and Salux publish documents by series, in prose: "серии LB, LT",
  "ССдВз 1Ex 01; ССдВз 1Ex 02 db". There are only 17 such documents, so they are
  matched through the rule table below rather than by parsing the prose. A table
  of rules can be read and corrected; a parser of sentences cannot. Salux's own
  registry warns that binding by series needs an article-to-series table from the
  supplier, so every series link is marked as such and can be audited.

Dekomo has no registry of its own; what it has is an archive of scans collected
from the brands, and two files inside it that name articles. See the Dekomo
section below for what is read and what is left alone.

SWG and ViaSvet publish nothing usable, so their products get no documents.
"""

from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

import openpyxl
import pandas as pd
from pymysql.connections import Connection

from staging.db import fetch_all, fetch_one
from staging.importers.jazzway_feed import order_code_for
from staging.pricing import fold

ARLIGHT_REGISTRY = r"D:\projects\1C\Арлайт\Reestr_dokumentov_sootvetstviya_Svetoyar.xlsx"
# The dealer portal's own export (assets.transistor.ru, Version 3), downloaded
# 30.09.2026. It carries the registry card id of every document, so the official
# FSA link is built rather than guessed, and lists the articles each covers - a
# far fuller source than the hand-kept registry above, which it replaces.
ARLIGHT_JSON = r"D:\projects\1C\certificates.json"
CRYSTAL_REGISTRY = r"D:\projects\1C\crystal\Реестр_сертификатов_LED_CRYSTAL(2).xlsx"
SALUX_REGISTRY = r"D:\projects\1C\Салюкс\Реестр_сертификатов_Салюкс_обновлён.xlsx"
JAZZWAY_REGISTRY = r"D:\projects\1C\Джазвея\Jazzway_реестр_документов_24-09-2026.xlsx"
# The 1C report read before the proper registry arrived. It is worse - no scans,
# some documents expired - but it covers 2,850 articles against the registry's
# 1,860, so it still answers for the ones the registry leaves out.
JAZZWAY_LEGACY_REGISTRY = r"D:\projects\1C\Джазвея\Джаз-Вей - Сертификаты номенклатуры.xlsx"

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


# The dealer portal numbers a document's kind. 1 is a certificate, 2 a
# declaration, 3 a letter (an отказное - the product needs no mandatory
# document), 4 a placeholder for one not issued yet.
ARLIGHT_JSON_KIND = {1: "certificate", 2: "declaration", 3: "refusal_letter", 4: "other"}
ARLIGHT_JSON_KIND_LABEL = {1: "Сертификат", 2: "Декларация", 3: "Отказное письмо", 4: "В ожидании"}
# How pub.fsa.gov.ru addresses a card, given the registry id the feed carries.
ARLIGHT_FSA_PATH = {1: "rss/certificate", 2: "rds/declaration"}


def _arlight_json_date(value: str | None) -> date | None:
    """The feed writes ISO dates (2024-08-14); a missing one comes as null."""
    text = _text(value)
    if not text:
        return None
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    return date(year, month, day)


def load_arlight_json(path: str = ARLIGHT_JSON) -> tuple[list[Certificate], list[tuple[str, str]]]:
    """Documents and (document code, article) links from the dealer export.

    The scan PDFs sit behind the dealer login (assets.transistor.ru answers 403
    without a session), so they are not stored as a public scan. The registry
    link is public and official, and is what the site needs; a document with a
    registry id gets its FSA card, the letters and pending rows do not.
    """
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    rows = payload["data"]["certificates"]

    certificates: list[Certificate] = []
    pairs: list[tuple[str, str]] = []
    for row in rows:
        code = _text(str(row.get("code")))
        if not code:
            continue
        kind_id = row.get("type")
        number = _text(row.get("number"))
        registry_id = _text(row.get("link"))
        pending = kind_id == 4 or (number or "").lower().startswith("в ожида")

        registry_url = None
        if registry_id and kind_id in ARLIGHT_FSA_PATH:
            registry_url = f"https://pub.fsa.gov.ru/{ARLIGHT_FSA_PATH[kind_id]}/view/{registry_id}/common"

        if pending:
            link_status = "pending"
        elif registry_url:
            link_status = "official"
        elif kind_id == 3:
            # A letter states the goods need no mandatory document - there is no
            # register to link, and that is the answer, not a gap.
            link_status = "not_required"
        else:
            link_status = "unconfirmed"

        label = ARLIGHT_JSON_KIND_LABEL.get(kind_id, "Документ")
        if row.get("not_required") == 1 and kind_id in (1, 2):
            label += " (добровольная)"  # voluntary confirmation, not mandatory

        certificates.append(
            Certificate(
                code=code,
                doc_kind="refusal_letter" if kind_id == 3 else _kind(label),
                doc_kind_label=label,
                doc_number=number,
                scope_text=_text(row.get("textshort")) or _text(row.get("textfull")),
                regulation=None,
                valid_from=_arlight_json_date(row.get("registered")),
                valid_to=_arlight_json_date(row.get("dateto")),
                registry_url=registry_url,
                other_url=None,
                scan_url=None,  # the PDF is dealer-login only, not a public scan
                link_status=link_status,
                source="Дилерский портал Arlight (v3)",
                notes=None,
            )
        )
        for article in row.get("products") or []:
            sku = _text(article)
            if sku:
                pairs.append((code, sku))

    return certificates, pairs


# --- Jazzway ---------------------------------------------------------------

JAZZWAY_SHEET = "Сертификаты"
JAZZWAY_HEADER_ROW = 2  # two title rows sit above the header
JAZZWAY_ARTICLE = "Артикул Jazzway"


def load_jazzway_registry(path: str = JAZZWAY_REGISTRY) -> tuple[list[Certificate], list[tuple[str, str]]]:
    """Documents, and (document code, article) links.

    Jazzway sent a proper registry on 24.09.2026, replacing the 1C report we
    had been reading. It is better in the ways that matter: **every** record
    carries a scan, 4,639 of 6,297 also carry a link to the official register,
    and nothing in it is expired - which was the gap in the old file, where 54
    products had nothing but out-of-date documents.

    One row per article and document, 1,860 articles against 166 documents, so
    the documents are collapsed by their number. The number is written inside a
    longer title - "Декларация о соответствии №ЕАЭС N RU Д-HK.РА05.В.56809/25" -
    and it is the part after the № that identifies the document.

    Documents have no code of their own, so they are numbered JAZ-001 upwards by
    document number, which keeps the numbering stable between runs.
    """
    frame = pd.read_excel(
        path, sheet_name=JAZZWAY_SHEET, header=JAZZWAY_HEADER_ROW, engine="openpyxl"
    )
    frame.columns = [str(column).strip() for column in frame.columns]

    rows: list[dict[str, Any]] = []
    for record in frame.to_dict("records"):
        article = _text(record.get(JAZZWAY_ARTICLE))
        title = _text(record.get("Номер / название"))
        if not article or not title:
            continue
        number = title.split("№", 1)[1].strip() if "№" in title else title
        rows.append({**record, "article": article, "number": number, "title": title})

    codes = {
        number: f"JAZ-{position:03d}"
        for position, number in enumerate(sorted({row["number"] for row in rows}), start=1)
    }

    certificates: list[Certificate] = []
    seen: set[str] = set()
    pairs: list[tuple[str, str]] = []
    linked: set[tuple[str, str]] = set()

    for row in rows:
        number, code = row["number"], codes[row["number"]]
        if number not in seen:
            seen.add(number)
            registry = _text(row.get("Ссылка на реестр"))
            scan = _text(row.get("Скан / файл"))
            label = _text(row.get("Тип документа"))
            kind = _kind(label)
            official = _is_official(registry)
            certificates.append(
                Certificate(
                    code=code,
                    doc_kind=kind,
                    doc_kind_label=label,
                    doc_number=number,
                    scope_text=None,  # the registry names articles, not scope
                    regulation=None,
                    valid_from=_date(row.get("Действует с")),
                    valid_to=_date(row.get("Действует до")),
                    registry_url=registry if official else None,
                    other_url=registry if registry and not official else None,
                    scan_url=scan,
                    link_status=(
                        "official" if official
                        # A refusal letter states the goods need no certificate,
                        # so there is no register for it to be in.
                        else "not_required" if kind == "refusal_letter"
                        else "unofficial" if scan
                        else "unconfirmed"
                    ),
                    source="Реестр документов Jazzway, выгрузка 24.09.2026",
                    notes=_text(row.get("Статус на дату выгрузки")),
                )
            )
        if (code, row["article"]) not in linked:
            linked.add((code, row["article"]))
            pairs.append((code, row["article"]))

    return certificates, pairs


JAZZWAY_LEGACY_SHEET = "TDSheet"
JAZZWAY_LEGACY_ARTICLE = "Артикул (РМ)"


def load_jazzway_legacy(
    path: str = JAZZWAY_LEGACY_REGISTRY,
) -> tuple[list[Certificate], list[tuple[str, str]]]:
    """The older 1C report, keyed by document number rather than code.

    One row per article and document, the article written once and left blank
    on the rows below it.
    """
    frame = pd.read_excel(path, sheet_name=JAZZWAY_LEGACY_SHEET, header=None, engine="openpyxl")
    header_index = _header_row(frame, JAZZWAY_LEGACY_ARTICLE)
    headers = [_text(cell) or f"col{i}" for i, cell in enumerate(frame.iloc[header_index])]

    certificates: list[Certificate] = []
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    article: str | None = None

    for index in range(header_index + 1, len(frame)):
        row = dict(zip(headers, frame.iloc[index].tolist()))
        article = _text(row.get(JAZZWAY_LEGACY_ARTICLE)) or article
        number = _text(row.get("Сертификат.Номер"))
        if not article or not number:
            continue
        if number not in seen:
            seen.add(number)
            url = _text(row.get("Ссылка на ФСА"))
            label = _text(row.get("Сертификат.Тип сертификата"))
            kind = _kind(label)
            official = _is_official(url)
            certificates.append(
                Certificate(
                    code="",  # numbered later, across both sources
                    doc_kind=kind,
                    doc_kind_label=label,
                    doc_number=number,
                    scope_text=None,
                    regulation=None,
                    valid_from=_date(row.get("Сертификат.Дата начала срока действия")),
                    valid_to=_date(row.get("Сертификат.Дата окончания срока действия")),
                    registry_url=url if official else None,
                    other_url=url if url and not official else None,
                    scan_url=None,
                    link_status=(
                        "official" if official
                        else "not_required" if kind == "refusal_letter"
                        else "unconfirmed"
                    ),
                    source="Выгрузка «Сертификаты номенклатуры» из 1С Джаз-Вей, 27.08.2026",
                )
            )
        pairs.append((number, article))
    return certificates, pairs


def load_jazzway(path: str = JAZZWAY_REGISTRY) -> tuple[list[Certificate], list[tuple[str, str]]]:
    """Both Jazzway sources, the newer one winning.

    The September registry is the better document: every record carries a scan,
    most carry a register link, nothing in it has expired. But it covers 1,860
    articles where the August report covered 2,850, and an article with an old
    document is better served than one with none. So the report fills the gaps,
    and where both describe the same document the registry's version is kept.
    """
    registry_certs, registry_pairs = load_jazzway_registry(path)
    number_of_code = {cert.code: cert.doc_number for cert in registry_certs}
    by_number = {cert.doc_number: cert for cert in registry_certs}
    covered = {article for _, article in registry_pairs}

    legacy_certs, legacy_pairs = load_jazzway_legacy()
    extra_pairs = [(number, article) for number, article in legacy_pairs if article not in covered]
    needed = {number for number, _ in extra_pairs}
    for cert in legacy_certs:
        if cert.doc_number in needed and cert.doc_number not in by_number:
            by_number[cert.doc_number] = cert

    # Number every document once, across both sources, so a code means the same
    # thing whichever file it came from.
    codes = {number: f"JAZ-{position:03d}" for position, number in enumerate(sorted(by_number), start=1)}
    certificates = []
    for number, cert in by_number.items():
        cert.code = codes[number]
        certificates.append(cert)

    pairs: list[tuple[str, str]] = []
    seen_pairs: set[tuple[str, str]] = set()
    numbered = [(number_of_code.get(code, code), article) for code, article in registry_pairs]
    for number, article in numbered + extra_pairs:
        code = codes.get(number)
        if code and (code, article) not in seen_pairs:
            seen_pairs.add((code, article))
            pairs.append((code, article))
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


# Sent by the supplier on 24.09.2026, after we asked what covered the КСдУ
# complexes. It is not in their published registry file yet, so it is written
# here the way the LED Crystal replacement is.
SALUX_KSDU = Certificate(
    code="SAL-11",
    doc_kind="declaration",
    doc_kind_label="Декларация о соответствии",
    doc_number="ЕАЭС N RU Д-RU.РА02.В.15175/25",
    scope_text="Осветительные комплексы светодиодные серии КСдУ, торговой марки SALUX",
    regulation="ТР ТС 004/2011; ТР ТС 020/2011",
    valid_from=date(2025, 2, 20),
    valid_to=date(2030, 2, 19),
    registry_url="https://pub.fsa.gov.ru/rds/declaration/view/19917832/common",
    other_url=None,
    scan_url=None,
    link_status="official",
    source="Прислана поставщиком 24.09.2026",
    notes="ТУ 27.40.39-013-10656537-2025, серийный выпуск.",
)


def load_salux(path: str = SALUX_REGISTRY) -> list[Certificate]:
    certificates = [SALUX_KSDU]
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


# --- Dekomo ----------------------------------------------------------------

# Dekomo publishes no registry of its own, and until now its 191,720 products
# carried no documents at all. On 29.09.2026 the client forwarded an archive of
# scans collected from the brands - 89 files covering some twenty-five of them.
# Two files in it are article-level tables rather than scans, and those are what
# is read here: EGLO's own article-to-document sheet, and an Apeyron stock
# report that names the document on every line. The rest of the archive binds a
# document to a brand or a product type rather than to articles, which this
# table cannot express, so it stays on disk until the client says how those
# should be shown.
#
# Both files write the bare article while Dekomo's price list prefixes it with
# the brand - EG_83998, AR_A8392AP-2SS. `manufacturer_code` holds the bare form.
# One bare article can belong to several price list rows, so a document reaches
# every row that carries it.

DEKOMO_ARCHIVE = r"D:\projects\1C\Декомо\Сертификаты и декларации Декомо"
DEKOMO_EGLO_REGISTRY = DEKOMO_ARCHIVE + r"\соответствие артикулов ЭГЛО сертификатам!!!(1).xlsx"
DEKOMO_APEYRON_REGISTRY = DEKOMO_ARCHIVE + r"\Apeyron_electrics_OGM_реестр.xlsx"

# Which brands each file speaks for. An article is matched only inside its own
# brands: Dekomo articles are short and repeat across brands, and a document
# hung on the wrong product is worse than no document at all. The Apeyron report
# covers six brands; we carry three of them.
DEKOMO_SOURCE_BRANDS = {
    "eglo": ("EGLO",),
    "apeyron": ("APEYRON ELECTRICS", "APEYRON CLOCK", "OGM"),
}


def _dekomo_article(value: Any) -> str | None:
    """The article as the files write it: whole numbers arrive as floats, and a
    few are typed with Excel's leading apostrophe ("'GL-556")."""
    if isinstance(value, float) and not pd.isna(value) and value.is_integer():
        value = int(value)
    text = _text(value)
    if not text:
        return None
    return text.lstrip("'").strip() or None


def _short_year_date(value: Any) -> date | None:
    """Apeyron writes dates as 14.08.25, where `_date` wants four digits."""
    full = _date(value)
    if full:
        return full
    match = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{2})", _text(value) or "")
    if not match:
        return None
    day, month, year = (int(part) for part in match.groups())
    return date(2000 + year, month, day)


# The two document columns are filled by hand and do not always hold a document.
# Some cells say there is none ("нет сертификата", "не подлежит сертификации"),
# some give the reason one is not needed ("аксессуар", "крепление"), and two name
# documents that have since been annulled. None of these may reach a product
# page as a certificate, so they are dropped rather than stored: a product with
# nothing to show is correct, a product showing "нет сертификата" as its document
# is not. Everything here is compared through `fold`, because one of the two
# spellings of "санкционный код" starts with a Latin c.
EGLO_NOT_A_DOCUMENT = frozenset({
    "нет документа", "нет сертификата", "нет сс", "не подлежит",
    "не подлежит сертификации", "отриц решение", "отказное письмо",
    "аксессуар", "зигби", "крепление", "прожектор", "санкционный код",
})


def _eglo_document_number(value: Any) -> str | None:
    """The document number, or None where the cell is not a document."""
    number = _text(value)
    if not number:
        return None
    folded = fold(number)
    if folded in EGLO_NOT_A_DOCUMENT or "аннулир" in folded:
        return None
    return number


EGLO_SOURCE = "Таблица соответствия артикулов EGLO сертификатам, архив Декомо 29.09.2026"


def load_dekomo_eglo(
    path: str = DEKOMO_EGLO_REGISTRY,
) -> tuple[list[Certificate], list[tuple[str, str]]]:
    """Documents, and (document code, bare article) links.

    One row per article, naming a certificate and usually a declaration with the
    validity of each. 20,738 rows resolve to 76 certificates and 14 declarations:
    the sheet repeats the document on every article it covers.

    Both links share one cell, separated by a line break, and are told apart by
    their path - the register files certificates under /rss/certificate/ and
    declarations under /rds/declaration/. 9,020 rows carry no link; the document
    is still recorded, without one.
    """
    frame = pd.read_excel(path, sheet_name=0, header=0, engine="openpyxl")

    documents: dict[str, dict[str, Any]] = {}
    pairs: set[tuple[str, str]] = set()

    for record in frame.to_dict("records"):
        values = list(record.values())
        article = _dekomo_article(values[0])
        if not article:
            continue
        urls = [url.strip() for url in str(values[7]).split("\n")] if _text(values[7]) else []
        certificate_url = next((url for url in urls if "/certificate/" in url), None)
        declaration_url = next((url for url in urls if "/declaration/" in url), None)

        for number, kind, label, valid_from, valid_to, url in (
            (_eglo_document_number(values[1]), "certificate", "Сертификат соответствия",
             values[3], values[4], certificate_url),
            (_eglo_document_number(values[2]), "declaration", "Декларация о соответствии",
             values[5], values[6], declaration_url),
        ):
            if not number:
                continue
            # A few cells name a decision or an information letter rather than a
            # certificate. The column says which document it ought to be, the
            # text says what it is, and the text wins.
            if "письмо" in fold(number) or "решение" in fold(number):
                kind, label = "other", "Письмо / решение"
            documents.setdefault(number, {
                "kind": kind,
                "label": label,
                "valid_from": _date(valid_from),
                "valid_to": _date(valid_to),
                "url": url,
            })
            # The link cell is empty on most rows, so the first row that carries
            # one answers for the document.
            if url and not documents[number]["url"]:
                documents[number]["url"] = url
            pairs.add((number, article))

    codes = {
        number: f"DEK-EGL-{position:03d}"
        for position, number in enumerate(sorted(documents), start=1)
    }
    certificates = [
        Certificate(
            code=codes[number],
            doc_kind=document["kind"],
            doc_kind_label=document["label"],
            doc_number=number,
            scope_text=None,  # the sheet names articles, not scope
            regulation=None,
            valid_from=document["valid_from"],
            valid_to=document["valid_to"],
            registry_url=document["url"] if _is_official(document["url"]) else None,
            other_url=None,
            scan_url=None,
            link_status="official" if _is_official(document["url"]) else "unconfirmed",
            source=EGLO_SOURCE,
        )
        for number, document in sorted(documents.items())
    ]
    return certificates, sorted((codes[number], article) for number, article in pairs)


APEYRON_SOURCE = "Отчёт «Apeyron electrics OGM», остатки на 28.08.2026, архив Декомо"
APEYRON_FIRST_DATA_ROW = 9  # the header spans rows 7 and 8
APEYRON_COLUMNS = {"article": 2, "brand": 4, "number": 6, "issued": 7,
                   "authority": 8, "valid_to": 9, "label": 10}


def _apeyron_registry_urls(path: str) -> dict[int, str]:
    """Sheet row -> register link.

    The links are HYPERLINK() formulas rather than stored hyperlinks, so a
    data_only read hands back only their caption, "Открыть карточку".
    """
    with zipfile.ZipFile(path) as archive:
        sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8", "replace")
    found = re.findall(
        r'<(?:\w+:)?c r="M(\d+)"[^>]*>\s*<(?:\w+:)?f>HYPERLINK\("([^"]+)"', sheet
    )
    return {int(row): url for row, url in found}


def load_dekomo_apeyron(
    path: str = DEKOMO_APEYRON_REGISTRY,
) -> tuple[list[Certificate], list[tuple[str, str]]]:
    """Documents, and (document code, bare article) links.

    A stock report rather than a registry: one row per article, with the
    document that covers it written alongside. 3,120 rows name 60 documents,
    1,610 of them carrying a link to the state register. 1,138 rows name no
    document at all and are passed over.

    A third of the documents are refusal and explanatory letters, which state
    the goods need no certificate. They have no register entry by definition, so
    they are marked `not_required` rather than left unconfirmed.
    """
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    sheet = workbook[workbook.sheetnames[0]]
    urls = _apeyron_registry_urls(path)

    documents: dict[str, dict[str, Any]] = {}
    pairs: set[tuple[str, str]] = set()

    for row_number, row in enumerate(
        sheet.iter_rows(min_row=APEYRON_FIRST_DATA_ROW, values_only=True),
        start=APEYRON_FIRST_DATA_ROW,
    ):
        def column(name: str) -> Any:
            position = APEYRON_COLUMNS[name]
            return row[position] if position < len(row) else None

        article = _dekomo_article(column("article"))
        number = _text(column("number"))
        if not article or not number:
            continue
        label = _text(column("label"))
        documents.setdefault(number, {
            "label": label,
            "kind": _kind(label),
            "valid_from": _short_year_date(column("issued")),
            "valid_to": _short_year_date(column("valid_to")),
            "authority": _text(column("authority")),
            "url": urls.get(row_number),
        })
        if urls.get(row_number) and not documents[number]["url"]:
            documents[number]["url"] = urls[row_number]
        pairs.add((number, article))

    workbook.close()

    codes = {
        number: f"DEK-APR-{position:03d}"
        for position, number in enumerate(sorted(documents), start=1)
    }
    certificates = []
    for number, document in sorted(documents.items()):
        official = _is_official(document["url"])
        # "Отказное письмо" and "Разъяснительное письмо" both say the product
        # falls outside mandatory certification. Neither is in a register.
        exempt = (
            document["kind"] == "refusal_letter"
            or "письмо" in (document["label"] or "").lower()
        )
        certificates.append(
            Certificate(
                code=codes[number],
                doc_kind=document["kind"],
                doc_kind_label=document["label"],
                doc_number=number,
                scope_text=None,
                regulation=None,
                valid_from=document["valid_from"],
                valid_to=document["valid_to"],
                registry_url=document["url"] if official else None,
                other_url=document["url"] if document["url"] and not official else None,
                scan_url=None,
                link_status="official" if official else "not_required" if exempt else "unconfirmed",
                source=APEYRON_SOURCE,
                notes=document["authority"],
            )
        )
    return certificates, sorted((codes[number], article) for number, article in pairs)


def load_dekomo() -> tuple[list[Certificate], dict[str, list[tuple[str, str]]]]:
    """Every Dekomo document, and its links grouped by the file they came from.

    The grouping is kept because each file answers for its own brands, and the
    article has to be resolved inside them.
    """
    eglo_certificates, eglo_pairs = load_dekomo_eglo()
    apeyron_certificates, apeyron_pairs = load_dekomo_apeyron()
    return (
        eglo_certificates + apeyron_certificates,
        {"eglo": eglo_pairs, "apeyron": apeyron_pairs},
    )


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
        # Осветительные комплексы КСдУ, declared as a series in February 2025.
        *_series("SAL-11", "КСдУ", "02", "03"),
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


def dekomo_links(
    conn: Connection, pairs_by_source: dict[str, list[tuple[str, str]]]
) -> list[tuple[str, str, str, str | None]]:
    """Registry links for the articles we actually carry.

    The two source files write the bare article - 83998 - while Dekomo's price
    list writes EG_83998, so the join runs through `manufacturer_code`. One bare
    article can sit on several price list rows, and the document covers all of
    them, so a pair can produce more than one link.

    The lookup is built per source file and restricted to that file's brands.
    Dekomo articles are short and repeat across brands, so an unrestricted join
    would hang EGLO's certificate on someone else's product.
    """
    links: list[tuple[str, str, str, str | None]] = []
    seen: set[tuple[str, str]] = set()

    for source, pairs in pairs_by_source.items():
        brands = DEKOMO_SOURCE_BRANDS[source]
        placeholders = ", ".join(["%s"] * len(brands))
        index: dict[str, list[str]] = {}
        for row in fetch_all(
            conn,
            f"""
            SELECT UPPER(TRIM(sp.manufacturer_code)) AS article, sp.supplier_sku
            FROM supplier_products sp JOIN suppliers s ON s.id = sp.supplier_id
            WHERE s.code = 'dekomo' AND sp.manufacturer_code IS NOT NULL
              AND UPPER(TRIM(sp.brand)) IN ({placeholders})
            """,
            brands,
        ):
            index.setdefault(row["article"], []).append(row["supplier_sku"])

        for code, article in pairs:
            for sku in index.get(article.upper(), ()):
                if (code, sku) not in seen:
                    seen.add((code, sku))
                    links.append((code, sku, "explicit", None))

    return links


def load_all(conn: Connection, progress: Callable[[str], None] = print) -> list[LoadResult]:
    results = []

    certs, pairs = load_arlight_json()
    progress(f"Арлайт: {len(certs)} документов, {len(pairs):,} связей с артикулами")
    results.append(save(conn, "arlight", certs, [(c, s, "explicit", None) for c, s in pairs]))

    certs, pairs = load_jazzway()
    links = jazzway_links(conn, pairs)
    progress(f"Jazzway: {len(certs)} документов, {len(links):,} связей с артикулами")
    results.append(save(conn, "jazzway", certs, links))

    certs, pairs = load_dekomo()
    links = dekomo_links(conn, pairs)
    progress(f"Декомо: {len(certs)} документов, {len(links):,} связей с артикулами")
    results.append(save(conn, "dekomo", certs, links))

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
