from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / "config.env"

if ENV_FILE.exists():
    load_dotenv(ENV_FILE)
else:
    load_dotenv()


@dataclass(frozen=True)
class DatabaseConfig:
    host: str
    port: int
    user: str
    password: str
    database: str

    @classmethod
    def from_env(cls) -> "DatabaseConfig":
        return cls(
            host=os.getenv("MYSQL_HOST", "127.0.0.1"),
            port=int(os.getenv("MYSQL_PORT", "3306")),
            user=os.getenv("MYSQL_USER", "root"),
            password=os.getenv("MYSQL_PASSWORD", ""),
            database=os.getenv("MYSQL_DATABASE", "svetoyar_staging"),
        )


DEKOMO_CSV_PATH = os.getenv(
    "DEKOMO_CSV_PATH",
    r"D:\projects\1C\Декомо\content_09_09_2026_13_35.xls",
)
DEKOMO_CSV_DEFAULT = DEKOMO_CSV_PATH

JAZZWAY_XLSX_DEFAULT = os.getenv(
    "JAZZWAY_XLSX_PATH",
    r"D:\projects\1C\Джазвея\11.08 Остатки для клиента.xlsx",
)

JAZZWAY_YML_URL = os.getenv(
    "JAZZWAY_YML_URL",
    "https://www.jazz-way.com/bitrix/catalog_export/export_all.xml",
)

VIASVET_XLSX_DEFAULT = os.getenv(
    "VIASVET_XLSX_PATH",
    r"D:\projects\1C\Виа Свет\ViaSvet_led_профиль_блоки_питания_лента_ПОСТУПЛЕНИЕ5.xlsx",
)
VIASVET_PHOTOS_DIR = os.getenv(
    "VIASVET_PHOTOS_DIR",
    r"D:\projects\1C\Виа Свет\на сайт",
)

CRYSTAL_XLS_DEFAULT = os.getenv(
    "CRYSTAL_XLS_PATH",
    r"D:\projects\1C\crystal\ПРАЙС LEDCRYSTAL от 05.08.2026.xls",
)

# Arrived misnamed .pdf and renamed to .xlsx on 2026-09-12. The importer names
# the openpyxl engine explicitly, so the extension does not matter - the path does.
SALUX_XLSX_DEFAULT = os.getenv(
    "SALUX_XLSX_PATH",
    r"D:\projects\1C\Салюкс\Прайс_лист_Дистрибьютор_Июнь_2026_Салюкс.xlsx",
)

# Svetoyar's own price list for Salux-made goods - our articles and our names,
# not a supplier feed. Still filed under the ViaSvet folder, where it arrived.
OWN_PRICE_XLSX_DEFAULT = os.getenv(
    "OWN_PRICE_XLSX_PATH",
    r"D:\projects\1C\Виа Свет\светнн1.xlsx",
)

# Arlight ships two files: an XML with photos, barcodes and customs codes, and
# an Excel price with stock and descriptions. The 11.09.2026 price carries the
# corrected descriptions the client asked for.
ARLIGHT_XML_DEFAULT = os.getenv(
    "ARLIGHT_XML_PATH",
    r"D:\projectsC\Арлайт\products.xml",
)

ARLIGHT_XLSX_DEFAULT = os.getenv(
    "ARLIGHT_XLSX_PATH",
    r"D:\projectsC\Арлайт\Прайс Арлайт.xlsx",
)

# SWG's export URL embeds a private token - anyone holding it can pull their
# whole price feed. It lives in config.env (gitignored), never in the source.
SWG_YML_URL = os.getenv("SWG_YML_URL", "")

SCHEMA_FILE = PROJECT_ROOT / "database" / "staging_schema.sql"
