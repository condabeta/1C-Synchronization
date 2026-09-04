from __future__ import annotations

import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator, Iterable

import pymysql
from pymysql.connections import Connection
from pymysql.cursors import DictCursor

from staging.config import DatabaseConfig


_UNSET = object()


def get_connection(
    config: DatabaseConfig | None = None,
    database: str | None | object = _UNSET,
) -> Connection:
    cfg = config or DatabaseConfig.from_env()
    kwargs: dict[str, Any] = {
        "host": cfg.host,
        "port": cfg.port,
        "user": cfg.user,
        "password": cfg.password,
        "charset": "utf8mb4",
        "cursorclass": DictCursor,
        "autocommit": False,
    }
    if database is _UNSET:
        kwargs["database"] = cfg.database
    elif database is not None:
        kwargs["database"] = database
    return pymysql.connect(**kwargs)


@contextmanager
def db_session(config: DatabaseConfig | None = None) -> Generator[Connection, None, None]:
    conn = get_connection(config)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _clean_statement(statement: str) -> str:
    lines = [
        line
        for line in statement.splitlines()
        if line.strip() and not line.strip().startswith("--")
    ]
    return "\n".join(lines).strip()


def split_sql_statements(sql_text: str) -> list[str]:
    statements: list[str] = []
    buffer: list[str] = []
    in_single = False
    in_double = False
    escape = False

    for char in sql_text:
        if escape:
            buffer.append(char)
            escape = False
            continue

        if char == "\\":
            buffer.append(char)
            escape = True
            continue

        if char == "'" and not in_double:
            in_single = not in_single
            buffer.append(char)
            continue

        if char == '"' and not in_single:
            in_double = not in_double
            buffer.append(char)
            continue

        if char == ";" and not in_single and not in_double:
            cleaned = _clean_statement("".join(buffer))
            if cleaned:
                statements.append(cleaned)
            buffer = []
            continue

        buffer.append(char)

    tail = _clean_statement("".join(buffer))
    if tail:
        statements.append(tail)
    return statements


def execute_sql_file(conn: Connection, path: Path) -> int:
    sql_text = path.read_text(encoding="utf-8")
    sql_text = re.sub(r"/\*.*?\*/", "", sql_text, flags=re.DOTALL)
    executed = 0
    with conn.cursor() as cur:
        for index, statement in enumerate(split_sql_statements(sql_text), start=1):
            try:
                cur.execute(statement)
                executed += 1
            except Exception as exc:
                preview = " ".join(statement.split())[:160]
                raise RuntimeError(f"SQL statement #{index} failed: {exc}\n{preview}") from exc
    return executed


def fetch_one(conn: Connection, query: str, params: Iterable[Any] | None = None) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(query, params or ())
        return cur.fetchone()


def fetch_all(conn: Connection, query: str, params: Iterable[Any] | None = None) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(query, params or ())
        return list(cur.fetchall())
