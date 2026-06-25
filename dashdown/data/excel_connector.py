"""Excel connector (via openpyxl). Backed by an in-memory DuckDB for SQL.

Heavy/optional dependency: install with `pip install 'dashdown-md[excel]'`.

Each worksheet in the workbook becomes a DuckDB table named after the sheet (or
a name you choose), so you query a spreadsheet with the same SQL as any other
source. The first row of a sheet is treated as the header by default.

sources.yaml example:
    budget:
      type: excel
      path: data/budget.xlsx     # relative to the project root
      # sheets:                  # optional: pick / rename sheets
      #   sales: Sheet1          #   table `sales` <- worksheet "Sheet1"
      #   costs: Costs
      # or a plain list to include a subset under their own names:
      # sheets: [Sales, Costs]
      # header: true             # first row is the header (default true)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from dashdown.data.base import register_connector
from dashdown.data.tabular import TabularConnector, _dedupe_headers, _import_driver


@register_connector("excel")
class ExcelConnector(TabularConnector):
    extra = "excel"

    def _load_tables(self) -> dict[str, Any]:
        path = self.config.get("path")
        if not path:
            raise ValueError("excel connector requires 'path' in sources.yaml")
        openpyxl = _import_driver("openpyxl", "excel")
        import pandas as pd

        project_root: Path = self.config.get("_project_root", Path("."))
        resolved = (project_root / path).resolve()
        # data_only returns cached cell *values* rather than formulae; read_only
        # streams rows without loading the whole workbook into memory.
        wb = openpyxl.load_workbook(resolved, read_only=True, data_only=True)
        try:
            has_header = self.config.get("header", True)
            # Map of {table_name: worksheet_name}. Default: every sheet, named
            # after itself.
            selection = self._resolve_selection(wb.sheetnames)
            tables: dict[str, Any] = {}
            for table_name, sheet_name in selection.items():
                ws = wb[sheet_name]
                rows = [list(r) for r in ws.iter_rows(values_only=True)]
                if not rows:
                    tables[table_name] = pd.DataFrame()
                    continue
                if has_header:
                    header = _dedupe_headers(rows[0])
                    data = rows[1:]
                else:
                    header = [f"col{i}" for i in range(len(rows[0]))]
                    data = rows
                tables[table_name] = pd.DataFrame(data, columns=header)
            return tables
        finally:
            wb.close()

    def _resolve_selection(self, sheetnames: list[str]) -> dict[str, str]:
        sheets = self.config.get("sheets")
        if sheets is None:
            return {name: name for name in sheetnames}
        if isinstance(sheets, dict):
            return {str(table): str(sheet) for table, sheet in sheets.items()}
        if isinstance(sheets, (list, tuple)):
            return {str(name): str(name) for name in sheets}
        raise ValueError("excel connector 'sheets' must be a list or a mapping")
