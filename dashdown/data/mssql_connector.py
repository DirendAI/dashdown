"""Microsoft SQL Server / Azure SQL connector (via pyodbc).

Heavy/optional dependency: install with `pip install 'dashdown-md[mssql]'`, plus
Microsoft's ODBC driver on the host (the **ODBC Driver 18 for SQL Server** is the
default; 17 also works). On Debian/Ubuntu that's the `msodbcsql18` apt package; on
macOS `brew install msodbcsql18`.

pyodbc exposes the standard PEP 249 DB-API 2.0 interface, so this is a thin
subclass of the shared `DBAPIConnector` (see `dbapi.py`) — only the driver import
and how the connection string is assembled differ from PostgreSQL/MySQL.

Authentication — chosen by the `authentication` key (or inferred). All Azure AD
modes require ODBC Driver 18 (or 17.x ≥ 17.3):

- **SQL login** (default): `user` + `password` → ODBC `UID`/`PWD`.
- **Service principal** (Azure AD app registration): set `client_id` + `client_secret`
  (and optionally `tenant_id`). `authentication` defaults to
  `ActiveDirectoryServicePrincipal` when those are present, so the common case needs
  no extra key. `client_id`→`UID`, `client_secret`→`PWD`.
- **Managed identity** (Azure VM / App Service): `authentication: ActiveDirectoryMsi`
  (alias `ActiveDirectoryManagedIdentity`); an optional user-assigned identity goes in
  `client_id`.
- **Azure AD username/password**: `authentication: ActiveDirectoryPassword` with
  `user`/`password`.
- **Default credential chain** (env → managed identity → CLI → …):
  `authentication: ActiveDirectoryDefault`.
- **Integrated / interactive**: `ActiveDirectoryIntegrated` / `ActiveDirectoryInteractive`.

sources.yaml examples::

    # SQL login
    warehouse:
      type: mssql
      host: db.example.com        # alias: server
      port: 1433                  # optional (default 1433)
      database: analytics         # alias: dbname
      user: reader                # alias: uid
      password: secret            # alias: pwd
      encrypt: true               # default true (driver 18 default)
      trust_server_certificate: false

    # Azure SQL via service principal (client credentials)
    warehouse:
      type: mssql
      host: myserver.database.windows.net
      database: analytics
      client_id: ${AZURE_CLIENT_ID}
      client_secret: ${AZURE_CLIENT_SECRET}
      tenant_id: ${AZURE_TENANT_ID}     # optional
      # authentication: ActiveDirectoryServicePrincipal   # inferred from the pair

    # Azure SQL via managed identity
    warehouse:
      type: mssql
      host: myserver.database.windows.net
      database: analytics
      authentication: ActiveDirectoryMsi
      # client_id: <user-assigned-identity-client-id>     # optional

Escape hatches (override everything above):

    warehouse:
      type: mssql
      connection_string: "DRIVER={ODBC Driver 18 for SQL Server};SERVER=...;..."
      # or a URL:  url: mssql://reader:secret@db.example.com:1433/analytics
      # extra ODBC keywords, merged last:
      odbc:
        MultiSubnetFailover: "Yes"
      # connect_args:   # extra kwargs passed straight to pyodbc.connect()
      #   timeout: 30
"""
from __future__ import annotations

from typing import Any

from dashdown.data.base import register_connector
from dashdown.data.dbapi import DBAPIConnector, _import_driver, parse_db_url

#: Default ODBC driver name. 18 is current; users with only 17 installed set
#: `driver: "ODBC Driver 17 for SQL Server"`.
DEFAULT_ODBC_DRIVER = "ODBC Driver 18 for SQL Server"

#: `authentication` values that mean "Azure AD service principal" — when one of
#: these (or none) is set together with client_id/secret we infer service-principal
#: auth and map the pair onto UID/PWD.
_SERVICE_PRINCIPAL_AUTH = {
    "activedirectoryserviceprincipal",
    "serviceprincipal",
}


def _odbc_escape(value: str) -> str:
    """Quote an ODBC connection-string value when it needs it.

    ODBC values containing `;`, `{`, `}`, `=` or surrounding spaces must be wrapped
    in braces, with any literal `}` doubled. Plain values (the common case — a
    hostname, a database name) are returned untouched so the string stays readable.
    """
    if value == "" or any(c in value for c in ";{}=") or value != value.strip():
        return "{" + value.replace("}", "}}") + "}"
    return value


@register_connector("mssql")
class MSSQLConnector(DBAPIConnector):
    extra = "mssql"
    driver = "pyodbc"

    def _build_connection_string(self) -> str:
        """Assemble an ODBC connection string from the discrete config keys.

        Returns the user's `connection_string` verbatim if given; otherwise builds
        one from `host`/`port`/`database`/auth keys, with a `url` (if present)
        filling in host/port/user/password/database, and an `odbc` mapping merged
        last so an author can set any keyword we don't model.
        """
        cfg = self.config
        raw = cfg.get("connection_string") or cfg.get("dsn")
        if raw:
            return raw

        # A url fills in the discrete fields it carries (host/port/user/pass/db).
        url = cfg.get("url")
        url_parts = parse_db_url(url) if url else {}

        host = cfg.get("host") or cfg.get("server") or url_parts.get("host")
        port = cfg.get("port") or url_parts.get("port")
        database = cfg.get("database") or cfg.get("dbname") or url_parts.get("database")
        user = cfg.get("user") or cfg.get("uid") or url_parts.get("user")
        password = cfg.get("password") or cfg.get("pwd") or url_parts.get("password")

        authentication = cfg.get("authentication") or cfg.get("auth")
        client_id = cfg.get("client_id")
        client_secret = cfg.get("client_secret")
        tenant_id = cfg.get("tenant_id")

        # Service-principal convenience: a client_id/secret pair with no explicit
        # (or an explicitly service-principal) `authentication` maps onto UID/PWD
        # with the ActiveDirectoryServicePrincipal authenticator.
        norm_auth = str(authentication).replace(" ", "").lower() if authentication else None
        if client_id and client_secret and (
            authentication is None or norm_auth in _SERVICE_PRINCIPAL_AUTH
        ):
            authentication = authentication or "ActiveDirectoryServicePrincipal"
            # Some tenants want UID=<client_id>@<tenant_id>; honor tenant_id when given.
            user = f"{client_id}@{tenant_id}" if tenant_id else client_id
            password = client_secret
        elif client_id and user is None:
            # client_id without a secret (e.g. a user-assigned managed identity, or
            # an interactive login hint) maps onto UID with no password.
            user = client_id

        # SERVER takes host,port (ODBC uses a comma, not a colon, for the port).
        server = host or "localhost"
        if port:
            server = f"{server},{port}"

        # Build keyword → value pairs in a stable order. None/unset keys are omitted.
        pairs: list[tuple[str, Any]] = [
            ("DRIVER", "{" + (cfg.get("driver") or DEFAULT_ODBC_DRIVER) + "}"),
            ("SERVER", server),
            ("DATABASE", database),
        ]
        if authentication:
            pairs.append(("Authentication", authentication))
        if user is not None:
            pairs.append(("UID", user))
        if password is not None:
            pairs.append(("PWD", password))

        # Encrypt / TrustServerCertificate: accept bool or yes/no string.
        if "encrypt" in cfg:
            pairs.append(("Encrypt", _yes_no(cfg["encrypt"])))
        if "trust_server_certificate" in cfg or "trust_cert" in cfg:
            val = cfg.get("trust_server_certificate", cfg.get("trust_cert"))
            pairs.append(("TrustServerCertificate", _yes_no(val)))
        if cfg.get("connection_timeout") is not None:
            pairs.append(("Connection Timeout", cfg["connection_timeout"]))

        # Any extra ODBC keywords the author supplies win (merged last).
        for k, v in (cfg.get("odbc") or {}).items():
            pairs.append((k, v))

        parts = []
        for key, value in pairs:
            if value is None:
                continue
            # DRIVER is already brace-wrapped above; everything else gets escaped.
            sval = value if key == "DRIVER" else _odbc_escape(str(value))
            parts.append(f"{key}={sval}")
        return ";".join(parts)

    def _connect(self) -> Any:
        pyodbc = _import_driver("pyodbc", "mssql")
        conn_str = self._build_connection_string()
        kwargs = dict(self.config.get("connect_args") or {})
        return pyodbc.connect(conn_str, **kwargs)


def _yes_no(value: Any) -> str:
    """Normalize a bool / yes-no-ish value to the ODBC `Yes`/`No` literal."""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, str) and value.strip().lower() in ("yes", "true", "1", "on"):
        return "Yes"
    if isinstance(value, str) and value.strip().lower() in ("no", "false", "0", "off"):
        return "No"
    return "Yes" if value else "No"
