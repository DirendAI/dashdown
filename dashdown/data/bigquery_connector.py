"""Google BigQuery connector (via google-cloud-bigquery).

Heavy/optional dependency: install with `pip install 'dashdown-md[bigquery]'`.

google-cloud-bigquery ships a PEP 249 DB-API 2.0 wrapper
(`google.cloud.bigquery.dbapi`) over its native client, so this connector reuses
the shared `DBAPIConnector` plumbing (see `dbapi.py`) — `_connect()` builds an
authenticated client and wraps it in a DB-API connection. BigQuery's `commit()`
is a no-op and connections don't drop the way pooled SQL connections do, but the
shared retry/commit paths are harmless here.

Authentication:
- `credentials_path` → a service-account JSON key file (resolved relative to the
  project root). If omitted, Application Default Credentials are used (env var
  GOOGLE_APPLICATION_CREDENTIALS, `gcloud auth application-default login`, or the
  GCP metadata server).

sources.yaml example:
    warehouse:
      type: bigquery
      project: my-gcp-project           # billing/default project (optional with ADC)
      location: EU                       # optional dataset location
      credentials_path: secrets/sa.json  # optional service-account key
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from dashdown.data.base import IntrospectionUnsupported, QueryResult, register_connector
from dashdown.data.dbapi import DBAPIConnector, _import_driver


@register_connector("bigquery")
class BigQueryConnector(DBAPIConnector):
    extra = "bigquery"
    driver = "google.cloud.bigquery"

    # -- schema introspection (overrides the ANSI-information_schema default) ---
    #
    # BigQuery's INFORMATION_SCHEMA is **not** a top-level schema: every view must
    # be region- or dataset-qualified (`` `region-us`.INFORMATION_SCHEMA.TABLES ``
    # or `` `proj.dataset`.INFORMATION_SCHEMA.TABLES ``). So the shared default
    # (a bare `FROM information_schema.tables`) can't work — we qualify it from
    # config: a `dataset:` (scoped to that dataset) wins, else `location:` lists
    # every dataset in that region. Neither set → a clear, actionable error.

    def _information_schema_prefix(self) -> str | None:
        # BigQuery INFORMATION_SCHEMA must be region- or dataset-qualified, and
        # optionally project-qualified. Each path part is backticked *separately*
        # so a hyphenated GCP project id (`my-gcp-project`) isn't parsed as
        # subtraction — `` `proj`.`region-us`.INFORMATION_SCHEMA.TABLES ``.
        project = self.config.get("project")
        proj_prefix = f"`{project}`." if project else ""
        dataset = self.config.get("dataset")
        if dataset:
            return f"{proj_prefix}`{dataset}`.INFORMATION_SCHEMA"
        location = self.config.get("location")
        if location:
            return f"{proj_prefix}`region-{str(location).lower()}`.INFORMATION_SCHEMA"
        return None

    def list_tables(self) -> QueryResult:
        prefix = self._information_schema_prefix()
        if prefix is None:
            raise IntrospectionUnsupported(
                "bigquery introspection needs a 'dataset' (or 'location') in "
                "sources.yaml to qualify INFORMATION_SCHEMA. Or query it directly, "
                "e.g. `SELECT table_name FROM \\`my_dataset\\`.INFORMATION_SCHEMA.TABLES`."
            )
        res = self.query(
            "SELECT table_name, table_schema, table_type "
            f"FROM {prefix}.TABLES ORDER BY table_schema, table_name"
        )
        return QueryResult(columns=["table", "schema", "type"], rows=res.rows)

    def describe_table(self, table: str) -> QueryResult:
        from dashdown.data.introspect import sql_str_literal

        prefix = self._information_schema_prefix()
        if prefix is None:
            raise IntrospectionUnsupported(
                "bigquery introspection needs a 'dataset' (or 'location') in "
                "sources.yaml to qualify INFORMATION_SCHEMA."
            )
        res = self.query(
            "SELECT column_name, data_type, is_nullable "
            f"FROM {prefix}.COLUMNS WHERE table_name = {sql_str_literal(table)} "
            "ORDER BY table_schema, ordinal_position"
        )
        return QueryResult(columns=["column", "type", "nullable"], rows=res.rows)

    def _make_client(self, bigquery: Any) -> Any:
        kwargs: dict[str, Any] = {}
        if self.config.get("project"):
            kwargs["project"] = self.config["project"]
        if self.config.get("location"):
            kwargs["location"] = self.config["location"]

        cred_path = self.config.get("credentials_path") or self.config.get("credentials_file")
        if cred_path:
            from google.oauth2 import service_account

            project_root: Path = self.config.get("_project_root", Path("."))
            resolved = (project_root / cred_path).resolve()
            kwargs["credentials"] = service_account.Credentials.from_service_account_file(
                str(resolved)
            )
        kwargs.update(self.config.get("connect_args") or {})
        return bigquery.Client(**kwargs)

    def _connect(self) -> Any:
        bigquery = _import_driver("google.cloud.bigquery", "bigquery")
        # The DB-API wrapper lives in a submodule; import it explicitly.
        from google.cloud.bigquery import dbapi

        return dbapi.connect(self._make_client(bigquery))
