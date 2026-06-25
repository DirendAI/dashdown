"""Tests for the spreadsheet connectors (Excel + Google Sheets).

The shared `TabularConnector` base (DuckDB registration, NaN scrubbing, SQL
execution, lazy load) is exercised through a tiny in-test subclass that returns
DataFrames directly — no driver needed. Excel/Sheets specifics (header parsing,
sheet/worksheet selection, the missing-driver hint) are tested without their
optional drivers installed: `_load_tables` is driven through stubbed driver
modules, and helper logic (`_resolve_selection`, header de-duplication) is tested
directly.
"""
from __future__ import annotations

import sys
import types

import pandas as pd
import pytest

from dashdown.data.base import QueryResult, get_connector_type
from dashdown.data.tabular import TabularConnector, _dedupe_headers
from dashdown.data.excel_connector import ExcelConnector
from dashdown.data.sheets_connector import GoogleSheetsConnector


class _FakeTabular(TabularConnector):
    """Subclass that returns canned DataFrames instead of reading a file/API."""

    extra = "fake"

    def __init__(self, tables):
        super().__init__("t", {})
        self._tables = tables
        self.load_calls = 0

    def _load_tables(self):
        self.load_calls += 1
        return self._tables


class TestTabularBase:
    def test_query_against_registered_table(self):
        df = pd.DataFrame({"id": [1, 2, 3], "name": ["a", "b", "c"]})
        c = _FakeTabular({"people": df})
        result = c.query("SELECT name FROM people WHERE id = 2")
        assert isinstance(result, QueryResult)
        assert result.columns == ["name"]
        assert result.rows == [["b"]]

    def test_table_name_with_spaces(self):
        df = pd.DataFrame({"v": [10]})
        c = _FakeTabular({"Form Responses 1": df})
        result = c.query('SELECT SUM(v) AS total FROM "Form Responses 1"')
        assert result.rows == [[10]]

    def test_nan_becomes_null(self):
        df = pd.DataFrame({"a": [1.0, None], "b": ["x", None]})
        c = _FakeTabular({"t": df})
        rows = c.query("SELECT a, b FROM t ORDER BY a NULLS LAST").rows
        assert rows[1] == [None, None]

    def test_lazy_load_once(self):
        df = pd.DataFrame({"n": [1]})
        c = _FakeTabular({"t": df})
        assert c.load_calls == 0  # nothing loaded at construction
        c.query("SELECT * FROM t")
        c.query("SELECT * FROM t")
        assert c.load_calls == 1  # loaded once, connection reused

    def test_multiple_tables(self):
        c = _FakeTabular(
            {"a": pd.DataFrame({"x": [1]}), "b": pd.DataFrame({"y": [2]})}
        )
        rows = c.query("SELECT a.x, b.y FROM a, b").rows
        assert rows == [[1, 2]]

    def test_close_is_idempotent(self):
        c = _FakeTabular({"t": pd.DataFrame({"n": [1]})})
        c.query("SELECT * FROM t")
        c.close()
        c.close()  # must not raise


class TestDedupeHeaders:
    def test_blank_and_none_get_positional_names(self):
        assert _dedupe_headers(["a", None, "  "]) == ["a", "col1", "col2"]

    def test_duplicates_are_suffixed(self):
        assert _dedupe_headers(["x", "x", "x"]) == ["x", "x_1", "x_2"]


# --- Excel -----------------------------------------------------------------


def _install_fake_openpyxl(monkeypatch, sheets: dict[str, list]):
    """Register a fake `openpyxl` module whose load_workbook yields `sheets`."""

    class FakeWS:
        def __init__(self, rows):
            self._rows = rows

        def iter_rows(self, values_only=True):
            return iter(self._rows)

    class FakeWB:
        def __init__(self):
            self.sheetnames = list(sheets)

        def __getitem__(self, name):
            return FakeWS(sheets[name])

        def close(self):
            pass

    mod = types.ModuleType("openpyxl")
    mod.load_workbook = lambda *a, **k: FakeWB()
    monkeypatch.setitem(sys.modules, "openpyxl", mod)


class TestExcel:
    def test_loads_all_sheets_with_header(self, monkeypatch, tmp_path):
        _install_fake_openpyxl(
            monkeypatch,
            {
                "Sales": [("region", "amount"), ("north", 10), ("south", 5)],
                "Costs": [("item", "cost"), ("rent", 100)],
            },
        )
        f = tmp_path / "book.xlsx"
        f.write_text("")
        c = ExcelConnector("x", {"path": "book.xlsx", "_project_root": tmp_path})
        assert c.query("SELECT SUM(amount) AS t FROM Sales").rows == [[15]]
        assert c.query("SELECT cost FROM Costs").rows == [[100]]

    def test_sheet_rename_mapping(self, monkeypatch, tmp_path):
        _install_fake_openpyxl(
            monkeypatch, {"Sheet1": [("a",), (1,), (2,)]}
        )
        f = tmp_path / "book.xlsx"
        f.write_text("")
        c = ExcelConnector(
            "x",
            {"path": "book.xlsx", "sheets": {"nums": "Sheet1"}, "_project_root": tmp_path},
        )
        assert c.query("SELECT SUM(a) AS s FROM nums").rows == [[3]]

    def test_header_false_uses_positional_columns(self, monkeypatch, tmp_path):
        _install_fake_openpyxl(monkeypatch, {"S": [("x", 1), ("y", 2)]})
        f = tmp_path / "book.xlsx"
        f.write_text("")
        c = ExcelConnector(
            "x", {"path": "book.xlsx", "header": False, "_project_root": tmp_path}
        )
        assert c.query("SELECT col0, col1 FROM S ORDER BY col1").rows == [["x", 1], ["y", 2]]

    def test_missing_path_raises(self):
        c = ExcelConnector("x", {})
        with pytest.raises(ValueError, match="requires 'path'"):
            c.query("SELECT 1")

    def test_missing_driver_hint(self, monkeypatch, tmp_path):
        # Force openpyxl to look absent regardless of the dev venv: a `None` entry
        # in sys.modules makes importlib.import_module raise ImportError, so the
        # connector surfaces the friendly install hint (delitem alone would just
        # let an installed openpyxl re-import successfully).
        monkeypatch.setitem(sys.modules, "openpyxl", None)
        c = ExcelConnector("x", {"path": "book.xlsx", "_project_root": tmp_path})
        with pytest.raises(ImportError) as exc:
            c.query("SELECT 1")
        assert "dashdown-md[excel]" in str(exc.value)
        assert "openpyxl" in str(exc.value)

    def test_resolve_selection_list(self):
        c = ExcelConnector("x", {"sheets": ["A", "B"]})
        assert c._resolve_selection(["A", "B", "C"]) == {"A": "A", "B": "B"}

    def test_resolve_selection_default_is_all(self):
        c = ExcelConnector("x", {})
        assert c._resolve_selection(["A", "B"]) == {"A": "A", "B": "B"}

    def test_resolve_selection_bad_type(self):
        c = ExcelConnector("x", {"sheets": "Sheet1"})
        with pytest.raises(ValueError, match="list or a mapping"):
            c._resolve_selection(["Sheet1"])


# --- Google Sheets ---------------------------------------------------------


def _install_fake_gspread(monkeypatch, tabs: dict[str, list], record=None):
    class FakeWS:
        def __init__(self, title, values):
            self.title = title
            self._values = values

        def get_all_values(self):
            return self._values

    class FakeSpreadsheet:
        def worksheets(self):
            return [FakeWS(t, v) for t, v in tabs.items()]

        def worksheet(self, title):
            return FakeWS(title, tabs[title])

    class FakeClient:
        def open_by_key(self, key):
            if record is not None:
                record["key"] = key
            return FakeSpreadsheet()

        def open_by_url(self, url):
            if record is not None:
                record["url"] = url
            return FakeSpreadsheet()

    mod = types.ModuleType("gspread")

    def service_account(filename=None):
        if record is not None:
            record["filename"] = filename
        return FakeClient()

    mod.service_account = service_account
    monkeypatch.setitem(sys.modules, "gspread", mod)


class TestGoogleSheets:
    def test_open_by_key_and_query(self, monkeypatch):
        rec = {}
        _install_fake_gspread(
            monkeypatch,
            {"Responses": [["q", "score"], ["a", "5"], ["b", "3"]]},
            record=rec,
        )
        c = GoogleSheetsConnector("g", {"spreadsheet_id": "KEY123"})
        # values come through as text; CAST to total them.
        rows = c.query("SELECT SUM(CAST(score AS INT)) AS t FROM Responses").rows
        assert rows == [[8]]
        assert rec["key"] == "KEY123"

    def test_open_by_url(self, monkeypatch):
        rec = {}
        _install_fake_gspread(monkeypatch, {"S": [["a"], ["1"]]}, record=rec)
        c = GoogleSheetsConnector("g", {"url": "https://docs.google.com/d/X/edit"})
        c.query("SELECT * FROM S")
        assert rec["url"].endswith("/edit")

    def test_worksheet_rename(self, monkeypatch):
        _install_fake_gspread(
            monkeypatch, {"Form Responses 1": [["a"], ["1"], ["2"]]}
        )
        c = GoogleSheetsConnector(
            "g",
            {"spreadsheet_id": "K", "worksheets": {"responses": "Form Responses 1"}},
        )
        assert c.query("SELECT COUNT(*) AS n FROM responses").rows == [[2]]

    def test_credentials_path_passed(self, monkeypatch, tmp_path):
        rec = {}
        _install_fake_gspread(monkeypatch, {"S": [["a"], ["1"]]}, record=rec)
        key = tmp_path / "sa.json"
        key.write_text("{}")
        c = GoogleSheetsConnector(
            "g",
            {"spreadsheet_id": "K", "credentials_path": "sa.json", "_project_root": tmp_path},
        )
        c.query("SELECT * FROM S")
        assert rec["filename"].endswith("sa.json")

    def test_no_key_or_url_raises(self, monkeypatch):
        _install_fake_gspread(monkeypatch, {})
        c = GoogleSheetsConnector("g", {})
        with pytest.raises(ValueError, match="spreadsheet_id"):
            c.query("SELECT 1")

    def test_missing_driver_hint(self, monkeypatch):
        monkeypatch.delitem(sys.modules, "gspread", raising=False)
        c = GoogleSheetsConnector("g", {"spreadsheet_id": "K"})
        with pytest.raises(ImportError) as exc:
            c.query("SELECT 1")
        assert "dashdown-md[sheets]" in str(exc.value)
        assert "gspread" in str(exc.value)


class TestRegistration:
    def test_registered_type_names(self):
        assert get_connector_type("excel") is ExcelConnector
        assert get_connector_type("sheets") is GoogleSheetsConnector

    def test_entry_points_expose_connectors(self):
        from importlib import metadata
        from dashdown.data.base import ENTRY_POINT_GROUP

        names = {ep.name for ep in metadata.entry_points(group=ENTRY_POINT_GROUP)}
        assert {"excel", "sheets"} <= names
