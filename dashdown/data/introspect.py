"""Connector schema introspection — the dialect-aware backing for
``dashdown query --tables`` / ``--schema <table>``.

An authoring agent asks two questions constantly — "what tables exist?" and
"what columns does table *T* have?" — and today has to hand-write a
``SELECT * FROM t LIMIT 0`` (and know the dialect) to answer them. These helpers
let a connector answer directly.

The default implementation speaks **ANSI ``information_schema``**, which DuckDB and
every DB-API SQL warehouse (Postgres/MySQL/MSSQL/Snowflake) support — so the base
:class:`~dashdown.data.base.Connector` (and any future third-party *SQL* connector)
gets ``list_tables``/``describe_table`` for free. A non-SQL connector (Cube has no
SQL; DAX speaks DAX) or a dialect that needs qualification (BigQuery) overrides the
two methods instead.

**Security.** :func:`information_schema_columns` interpolates the table *name* as a
**quoted string literal** in a ``WHERE`` clause, escaped ``'`` → ``''`` exactly like
``render/pipeline.py::_substitute_params``. It is never spliced in as a SQL
*identifier*, so a crafted table name is matched as inert data against
``information_schema.columns`` and can't break out of the literal.
"""
from __future__ import annotations

from typing import Callable

from dashdown.data.base import QueryResult

#: Schemas/catalogs that hold engine metadata rather than user tables — excluded
#: from ``list_tables``. This is the *union* of the system schemas across
#: DuckDB / Postgres / MySQL / MSSQL / Snowflake; the comparison is
#: case-insensitive (MSSQL's ``INFORMATION_SCHEMA`` is upper-cased), so listing
#: them here once covers every ``information_schema``-speaking backend.
SYSTEM_SCHEMAS = frozenset(
    {
        "information_schema",
        "pg_catalog",
        "pg_toast",
        "mysql",
        "performance_schema",
        "sys",
    }
)


def sql_str_literal(value: str) -> str:
    """Quote a value as a SQL string literal, escaping embedded single quotes.

    The one escaping primitive these helpers use; mirrors the ``'`` → ``''`` rule
    in ``_substitute_params`` so the introspection path shares the framework's
    single, audited quoting convention.
    """
    return "'" + str(value).replace("'", "''") + "'"


def information_schema_tables(query: Callable[[str], QueryResult]) -> QueryResult:
    """List user tables/views via ``information_schema.tables``.

    *query* is the connector's own :meth:`~dashdown.data.base.Connector.query`
    (so dialect, connection, and locking are the connector's). Result columns are
    normalized to ``table`` / ``schema`` / ``type`` regardless of the engine's
    own ``information_schema`` column casing.
    """
    excluded = ", ".join(sql_str_literal(s) for s in sorted(SYSTEM_SCHEMAS))
    sql = (
        "SELECT table_name, table_schema, table_type "
        "FROM information_schema.tables "
        f"WHERE lower(table_schema) NOT IN ({excluded}) "
        "ORDER BY table_schema, table_name"
    )
    res = query(sql)
    return QueryResult(columns=["table", "schema", "type"], rows=res.rows)


def information_schema_columns(
    query: Callable[[str], QueryResult], table: str
) -> QueryResult:
    """Describe a table's columns via ``information_schema.columns``.

    The table name is matched as a **quoted string literal** (see the module
    docstring's security note), never spliced as an identifier. Result columns are
    normalized to ``column`` / ``type`` / ``nullable``.
    """
    sql = (
        "SELECT column_name, data_type, is_nullable "
        "FROM information_schema.columns "
        f"WHERE table_name = {sql_str_literal(table)} "
        "ORDER BY table_schema, ordinal_position"
    )
    res = query(sql)
    return QueryResult(columns=["column", "type", "nullable"], rows=res.rows)
