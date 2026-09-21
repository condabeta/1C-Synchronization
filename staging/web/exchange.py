"""The address 1C connects to.

In 1C's «Обмен с сайтом» the site is the server and 1C is the client: 1C opens
the connection, authenticates, and then either pushes files at us or asks for
ours. This is that endpoint, as a Flask blueprint - everything the protocol
needs except the parts that can only be settled against a live 1C:

    GET  /1c_exchange?type=catalog&mode=checkauth   -> success, cookie name, value
    GET  /1c_exchange?type=catalog&mode=init        -> zip=no, file_limit=...
    POST /1c_exchange?type=catalog&mode=file&filename=import.xml  -> the body is written
    GET  /1c_exchange?type=catalog&mode=import&filename=import.xml -> the file is read
    GET  /1c_exchange?type=catalog&mode=query       -> our catalogue as CommerceML

The protocol answers in plain text, first line "success" or "failure", which is
why nothing here returns JSON.

Two things are deliberately not finished, because guessing at them would be
worse than leaving them visible:

* **mode=import** accepts the file and reports success without reading it into
  staging. Which direction the client actually wants - 1C as the master of the
  catalogue, or our staging as the master - is still open, and it decides what
  importing means. The file is kept, so nothing is lost.
* **type=sale** (orders) is refused with a clear message. There is no orders
  table in this system yet; the site is not taking orders.

Credentials come from config.env (ONEC_EXCHANGE_USER / ONEC_EXCHANGE_PASSWORD).
With none set the endpoint refuses every request rather than defaulting to
something guessable.
"""

from __future__ import annotations

import base64
import re
import secrets
from pathlib import Path

from flask import Blueprint, Response, current_app, request

from staging.config import (
    ONEC_EXCHANGE_DIR,
    ONEC_EXCHANGE_PASSWORD,
    ONEC_EXCHANGE_USER,
)
from staging.db import db_session
from staging.publishing import commerceml

bp = Blueprint("exchange", __name__)

SESSION_COOKIE = "svetoyar_exchange"
# 1C splits anything larger into pieces and sends them one after another.
FILE_LIMIT = 10 * 1024 * 1024

# A filename arrives from outside and is used to build a path.
SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

_sessions: dict[str, bool] = {}


def _text(body: str, status: int = 200) -> Response:
    return Response(body, status=status, mimetype="text/plain; charset=utf-8")


def _failure(message: str) -> Response:
    # The protocol wants failure reported with 200 and a "failure" first line;
    # a 4xx makes 1C show "сервер недоступен" instead of the reason.
    return _text(f"failure\n{message}")


def _credentials_configured() -> bool:
    return bool(ONEC_EXCHANGE_USER and ONEC_EXCHANGE_PASSWORD)


def _check_basic_auth() -> bool:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:]).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return False
    user, _, password = decoded.partition(":")
    return secrets.compare_digest(user, ONEC_EXCHANGE_USER) and secrets.compare_digest(
        password, ONEC_EXCHANGE_PASSWORD
    )


def _authenticated() -> bool:
    token = request.cookies.get(SESSION_COOKIE)
    if token and _sessions.get(token):
        return True
    return _check_basic_auth()


def _exchange_dir() -> Path:
    path = Path(ONEC_EXCHANGE_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _resolve(filename: str) -> Path | None:
    """A path inside the exchange directory, or None if the name is not safe."""
    if not filename or not SAFE_FILENAME_RE.match(filename):
        return None
    return _exchange_dir() / filename


@bp.route("/1c_exchange", methods=["GET", "POST"])
def exchange() -> Response:
    if not _credentials_configured():
        return _failure(
            "Обмен не настроен: задайте ONEC_EXCHANGE_USER и ONEC_EXCHANGE_PASSWORD в config.env"
        )

    exchange_type = request.args.get("type", "")
    mode = request.args.get("mode", "")

    if exchange_type == "sale":
        return _failure("Обмен заказами не реализован: в системе нет заказов")
    if exchange_type != "catalog":
        return _failure(f"Неизвестный тип обмена: {exchange_type or '(пусто)'}")

    if mode == "checkauth":
        if not _check_basic_auth():
            return _failure("Неверный логин или пароль")
        token = secrets.token_hex(16)
        _sessions[token] = True
        return _text(f"success\n{SESSION_COOKIE}\n{token}")

    if not _authenticated():
        return _failure("Не авторизован")

    if mode == "init":
        # No zip: the files are written by us and read by 1C on the same
        # machine or over a local network, and an unzipped file is one less
        # thing to go wrong while this is being set up.
        return _text(f"zip=no\nfile_limit={FILE_LIMIT}")

    if mode == "file":
        path = _resolve(request.args.get("filename", ""))
        if path is None:
            return _failure("Недопустимое имя файла")
        # 1C sends a large file in pieces, appending each one.
        with path.open("ab") as handle:
            handle.write(request.get_data())
        return _text("success")

    if mode == "import":
        path = _resolve(request.args.get("filename", ""))
        if path is None or not path.exists():
            return _failure("Файл не найден")
        current_app.logger.info("1C prepared %s (%d bytes) for import", path.name, path.stat().st_size)
        # Accepted and kept. Reading it into staging waits on the client's
        # decision about which side owns the catalogue.
        return _text("success")

    if mode == "query":
        # Our catalogue, written fresh. This is the direction the client wants -
        # staging to 1C - and it is not part of the standard exchange, so the
        # 1C side has to be configured to fetch it.
        try:
            with db_session() as conn:
                stats = commerceml.export(conn, _exchange_dir())
        except Exception as exc:  # a failed export must say so, not return half a file
            current_app.logger.exception("CommerceML export failed")
            return _failure(f"Не удалось сформировать выгрузку: {exc}")
        body = (_exchange_dir() / "import.xml").read_text(encoding="utf-8")
        current_app.logger.info(
            "Served catalogue: %d products, %d offers", stats.products, stats.offers
        )
        return Response(body, mimetype="text/xml; charset=utf-8")

    return _failure(f"Неизвестный режим: {mode or '(пусто)'}")
