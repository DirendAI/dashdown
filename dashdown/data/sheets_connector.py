"""Google Sheets connector (via gspread). Backed by an in-memory DuckDB for SQL.

Heavy/optional dependency: install with `pip install 'dashdown-md[sheets]'`.

Each worksheet (tab) in the spreadsheet becomes a DuckDB table named after the
tab (or a name you choose), so you query a spreadsheet with the same SQL as any
other source. The first row of a tab is treated as the header by default. Cell
values come through as text (gspread returns strings) — `CAST(...)` in SQL where
you need numbers or dates.

Authentication uses a Google service account. Share the spreadsheet with the
service account's email, then point `credentials_path` at its JSON key (or omit
it to use gspread's default `~/.config/gspread/service_account.json`).

sources.yaml example:
    survey:
      type: sheets
      spreadsheet_id: 1AbC...XyZ          # the key from the sheet URL
      # or:  url: https://docs.google.com/spreadsheets/d/1AbC...XyZ/edit
      credentials_path: secrets/sa.json   # service-account key (relative to project)
      # worksheets:                       # optional: pick / rename tabs
      #   responses: Form Responses 1
      # or a plain list: worksheets: [Sheet1, Sheet2]
      # header: true                      # first row is the header (default true)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from dashdown.data.base import register_connector
from dashdown.data.tabular import TabularConnector, _dedupe_headers, _import_driver


@register_connector("sheets")
class GoogleSheetsConnector(TabularConnector):
    extra = "sheets"

    def _open_spreadsheet(self, gspread: Any) -> Any:
        project_root: Path = self.config.get("_project_root", Path("."))
        cred_path = self.config.get("credentials_path") or self.config.get("credentials_file")
        if cred_path:
            gc = gspread.service_account(filename=str((project_root / cred_path).resolve()))
        else:
            gc = gspread.service_account()

        if self.config.get("url"):
            return gc.open_by_url(self.config["url"])
        key = self.config.get("spreadsheet_id") or self.config.get("key")
        if key:
            return gc.open_by_key(str(key))
        raise ValueError(
            "sheets connector requires 'spreadsheet_id' (or 'url') in sources.yaml"
        )

    def _load_tables(self) -> dict[str, Any]:
        gspread = _import_driver("gspread", "sheets")
        import pandas as pd

        sh = self._open_spreadsheet(gspread)
        has_header = self.config.get("header", True)
        selection = self._resolve_selection([ws.title for ws in sh.worksheets()])

        tables: dict[str, Any] = {}
        for table_name, tab_name in selection.items():
            ws = sh.worksheet(tab_name)
            values = ws.get_all_values()
            if not values:
                tables[table_name] = pd.DataFrame()
                continue
            if has_header:
                header = _dedupe_headers(values[0])
                data = values[1:]
            else:
                header = [f"col{i}" for i in range(len(values[0]))]
                data = values
            tables[table_name] = pd.DataFrame(data, columns=header)
        return tables

    def _resolve_selection(self, titles: list[str]) -> dict[str, str]:
        worksheets = self.config.get("worksheets")
        if worksheets is None:
            return {title: title for title in titles}
        if isinstance(worksheets, dict):
            return {str(table): str(tab) for table, tab in worksheets.items()}
        if isinstance(worksheets, (list, tuple)):
            return {str(name): str(name) for name in worksheets}
        raise ValueError("sheets connector 'worksheets' must be a list or a mapping")
