"""DAX connector for Microsoft Fabric / Power BI Analysis Services.

Executes DAX queries via the Power BI REST API (executeDaxQueries endpoint).
Responses are returned as Arrow IPC streams and parsed with pyarrow.

Authentication priority:
1. Service principal (client_secret provided) → MSAL ConfidentialClientApplication
2. Interactive browser (tenant_id + client_id) → MSAL PublicClientApplication
3. Fallback → azure-identity DefaultAzureCredential

sources.yaml example:
    fabric:
      type: dax
      dataset_id: cfafbeb1-8037-4d0c-896e-a46fb27ff229
      workspace_id: null          # optional; if set uses workspace-scoped endpoint
      tenant_id: null
      client_id: null
      client_secret: null         # omit for interactive browser auth
"""
from __future__ import annotations

import io
import logging
import math
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import requests

from dashdown.data.base import Connector, QueryResult, register_connector

# Note: pyarrow is the optional `dashdown-md[dax]` extra (it parses the Arrow IPC
# response), so it is imported **lazily** inside `query()` rather than at module
# load — matching every other optional-dep connector. This keeps the module
# importable (e.g. for schema introspection, which doesn't touch pyarrow, or a
# direct import in tests) without the extra installed.

log = logging.getLogger(__name__)

_PBI_SCOPE = "https://analysis.windows.net/powerbi/api/.default"
_PBI_BASE = "https://api.powerbi.com/v1.0/myorg"


@register_connector("dax")
class DAXConnector(Connector):
    """Executes DAX queries against a Fabric / Power BI dataset."""

    def __init__(self, name: str, config: dict[str, Any]) -> None:
        super().__init__(name, config)
        self.dataset_id: str = config.get("dataset_id", "")
        self.workspace_id: str | None = config.get("workspace_id")
        if not self.dataset_id:
            raise ValueError("dax connector requires 'dataset_id' in sources.yaml")
        self._token: str | None = None
        self._msal_app: Any = None

    def _get_token(self) -> str:
        """Obtain a bearer token using MSAL or azure-identity."""
        tenant_id = self.config.get("tenant_id")
        client_id = self.config.get("client_id")
        client_secret = self.config.get("client_secret")
        scopes = [_PBI_SCOPE]

        if tenant_id and client_id and client_secret:
            from msal import ConfidentialClientApplication

            if self._msal_app is None:
                authority = f"https://login.microsoftonline.com/{tenant_id}"
                self._msal_app = ConfidentialClientApplication(
                    client_id, authority=authority, client_credential=client_secret,
                )
            result = self._msal_app.acquire_token_for_client(scopes=scopes)
            if "access_token" in result:
                return result["access_token"]
            raise RuntimeError(
                f"MSAL auth failed: {result.get('error_description', result)}"
            )

        if tenant_id and client_id:
            from msal import PublicClientApplication

            if self._msal_app is None:
                authority = f"https://login.microsoftonline.com/{tenant_id}"
                self._msal_app = PublicClientApplication(client_id, authority=authority)
            # Try silent first (cached token)
            accounts = self._msal_app.get_accounts()
            result = None
            if accounts:
                result = self._msal_app.acquire_token_silent(
                    scopes=scopes, account=accounts[0],
                )
            if not result or "access_token" not in result:
                result = self._msal_app.acquire_token_interactive(scopes=scopes)
            if "access_token" in result:
                return result["access_token"]
            raise RuntimeError(
                f"MSAL interactive auth failed: {result.get('error_description', result)}"
            )

        # Fallback to azure-identity
        try:
            from azure.identity import DefaultAzureCredential
        except ImportError as e:
            raise ImportError(
                "Provide tenant_id/client_id in sources.yaml for MSAL auth, "
                "or install azure-identity: pip install azure-identity"
            ) from e
        credential = DefaultAzureCredential()
        token = credential.get_token(_PBI_SCOPE)
        return token.token

    @property
    def _endpoint(self) -> str:
        if self.workspace_id:
            return (
                f"{_PBI_BASE}/groups/{self.workspace_id}"
                f"/datasets/{self.dataset_id}/executeDaxQueries"
            )
        return f"{_PBI_BASE}/datasets/{self.dataset_id}/executeDaxQueries"

    def _ensure_token(self) -> str:
        if self._token is None:
            self._token = self._get_token()
        return self._token

    def query(self, sql: str) -> QueryResult:
        """Execute a DAX query and return the result.

        Despite the parameter name 'sql', the string should be a DAX expression
        (e.g. `EVALUATE VALUES(MyTable)`).
        """
        try:
            import pyarrow as pa
        except ImportError as e:  # pragma: no cover - exercised when extra absent
            raise ImportError(
                "The 'dax' connector requires pyarrow to parse query results, which "
                "is not installed. Install it with: pip install 'dashdown-md[dax]'  "
                f"(underlying error: {e})"
            ) from e

        token = self._ensure_token()
        body = {"query": sql}
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        resp = requests.post(self._endpoint, json=body, headers=headers, timeout=120)

        if resp.status_code == 401:
            # Token may have expired; refresh once and retry.
            self._token = self._get_token()
            headers["Authorization"] = f"Bearer {self._token}"
            resp = requests.post(
                self._endpoint, json=body, headers=headers, timeout=120,
            )

        resp.raise_for_status()

        reader = pa.ipc.open_stream(io.BytesIO(resp.content))
        table = reader.read_all()

        columns = [_short_col(c) for c in table.column_names]

        if table.num_rows == 0:
            return QueryResult(columns=columns, rows=[])

        # Convert directly from Arrow to Python lists, skipping pandas/numpy
        # to avoid Decimal, NaN, and numpy-type JSON serialization issues.
        col_lists = [table.column(i).to_pylist() for i in range(table.num_columns)]
        rows = [
            [_clean_value(col_lists[c][r]) for c in range(len(columns))]
            for r in range(table.num_rows)
        ]
        return QueryResult(columns=columns, rows=rows)

    # -- schema introspection (DAX engine metadata, not SQL) ------------------
    #
    # A Fabric/Power BI model is a tabular model, not a SQL database, so the
    # information_schema default can't run. DAX exposes the model's own metadata
    # through the `INFO.VIEW.*` functions (the friendly, named-column form of the
    # `$SYSTEM` DMVs). `_short_col` already strips `Table[Column]` → `Column`, so
    # the result columns arrive as plain names — `INFO.VIEW.TABLES()` yields
    # `[Name]`, `INFO.VIEW.COLUMNS()` yields `[Table]`/`[Name]`/`[DataType]`
    # (verified against Microsoft Learn). Caveat: `INFO.VIEW.*` needs a current
    # engine and the caller's principal to have write/build permission on the
    # model — a read-only principal will see the query error surfaced as-is.

    def list_tables(self) -> QueryResult:
        res = self.query("EVALUATE INFO.VIEW.TABLES()")
        names = _pick_column(res, "Name")
        rows = [[n, None, "table"] for n in names]
        return QueryResult(columns=["table", "schema", "type"], rows=rows)

    def describe_table(self, table: str) -> QueryResult:
        # INFO.VIEW.COLUMNS() returns every column in the model; filter to the
        # requested table client-side (no DAX string-literal quoting needed).
        res = self.query("EVALUATE INFO.VIEW.COLUMNS()")
        idx = {c.lower(): i for i, c in enumerate(res.columns)}
        i_table, i_name, i_type = idx.get("table"), idx.get("name"), idx.get("datatype")
        rows = []
        for r in res.rows:
            if i_table is not None and str(r[i_table]) != table:
                continue
            name = r[i_name] if i_name is not None else None
            dtype = r[i_type] if i_type is not None else None
            rows.append([name, dtype, None])
        return QueryResult(columns=["column", "type", "nullable"], rows=rows)


def _pick_column(res: QueryResult, name: str) -> list[Any]:
    """Values of a result column matched case-insensitively, else ``[]``."""
    idx = {c.lower(): i for i, c in enumerate(res.columns)}
    i = idx.get(name.lower())
    return [r[i] for r in res.rows] if i is not None else []


def _clean_value(val: Any) -> Any:
    """Convert Arrow-native Python values to JSON-safe types."""
    if val is None:
        return None
    if isinstance(val, Decimal):
        return float(val)
    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
        return None
    if isinstance(val, (datetime, date)):
        return val.isoformat()
    return val


def _short_col(name: str) -> str:
    """Convert 'MyTable[Column]' to 'Column', leave plain names as-is."""
    if "[" in name and name.endswith("]"):
        return name[name.index("[") + 1 : -1]
    return name
