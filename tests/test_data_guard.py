"""The big-result guard: `data.max_rows` caps live data-API responses.

Covers the `data:` config block parsing, the capped `serialize_result` seam,
and the data API's truncation flag — plus the surfaces that stay uncapped
(static build snapshots via default serialize, cache intactness).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dashdown.data.base import QueryResult
from dashdown.project import DataConfig, parse_data_config
from dashdown.render.pipeline import (
    _library_keys,
    _query_def_cache,
    _result_cache,
    _stream_def_cache,
    serialize_result,
)
from dashdown.server import create_app


def _clear_caches():
    _query_def_cache.clear()
    _stream_def_cache.clear()
    _library_keys.clear()
    _result_cache.clear()


@pytest.fixture(autouse=True)
def _isolate():
    _clear_caches()
    yield
    _clear_caches()


# --------------------------------------------------------------------------- #
# config parsing
# --------------------------------------------------------------------------- #
class TestParseDataConfig:
    def test_default(self):
        cfg = parse_data_config(None)
        assert cfg.max_rows == DataConfig.max_rows == 10000

    def test_explicit(self):
        assert parse_data_config({"max_rows": 500}).max_rows == 500

    def test_zero_disables(self):
        assert parse_data_config({"max_rows": 0}).max_rows == 0

    def test_rejects_non_mapping(self):
        with pytest.raises(ValueError):
            parse_data_config("nope")

    def test_rejects_negative(self):
        with pytest.raises(ValueError):
            parse_data_config({"max_rows": -1})

    def test_rejects_bool(self):
        with pytest.raises(ValueError):
            parse_data_config({"max_rows": True})


# --------------------------------------------------------------------------- #
# serialize_result cap
# --------------------------------------------------------------------------- #
class TestSerializeCap:
    def _result(self, n: int) -> QueryResult:
        return QueryResult(columns=["n"], rows=[[i] for i in range(n)])

    def test_uncapped_by_default(self):
        payload = serialize_result(self._result(5))
        assert len(payload["rows"]) == 5
        assert "truncated" not in payload

    def test_cap_truncates_and_flags(self):
        payload = serialize_result(self._result(5), max_rows=3)
        assert len(payload["rows"]) == 3
        assert payload["truncated"] is True
        assert payload["total_rows"] == 5

    def test_cap_at_exact_size_is_not_truncated(self):
        payload = serialize_result(self._result(3), max_rows=3)
        assert len(payload["rows"]) == 3
        assert "truncated" not in payload


# --------------------------------------------------------------------------- #
# data API
# --------------------------------------------------------------------------- #
def _make_project(tmp: Path, dashdown_yaml: str) -> Path:
    (tmp / "pages").mkdir()
    (tmp / "pages" / "index.md").write_text(
        "# Rows\n\n"
        ":::query name=all_rows connector=main\n"
        "SELECT * FROM rows ORDER BY n\n"
        ":::\n\n"
        "<Table data={all_rows} />\n",
        encoding="utf-8",
    )
    (tmp / "data").mkdir()
    (tmp / "data" / "rows.csv").write_text(
        "n\n" + "".join(f"{i}\n" for i in range(10)), encoding="utf-8"
    )
    (tmp / "sources.yaml").write_text(
        "main:\n  type: csv\n  directory: data\n", encoding="utf-8"
    )
    (tmp / "dashdown.yaml").write_text(dashdown_yaml, encoding="utf-8")
    return tmp


def _fetch(project: Path) -> dict:
    client = TestClient(create_app(project))
    assert client.get("/").status_code == 200  # registers the page's query defs
    resp = client.get("/_dashdown/api/data/all_rows?_connector=main")
    assert resp.status_code == 200
    return resp.json()


def test_data_api_truncates_over_cap(tmp_path: Path) -> None:
    proj = _make_project(tmp_path, "title: T\ndata:\n  max_rows: 4\n")
    payload = _fetch(proj)
    assert len(payload["rows"]) == 4
    assert payload["truncated"] is True
    assert payload["total_rows"] == 10


def test_data_api_default_cap_leaves_small_results_alone(tmp_path: Path) -> None:
    payload = _fetch(_make_project(tmp_path, "title: T\n"))
    assert len(payload["rows"]) == 10
    assert "truncated" not in payload


def test_data_api_zero_disables_cap(tmp_path: Path) -> None:
    proj = _make_project(tmp_path, "title: T\ndata:\n  max_rows: 0\n")
    payload = _fetch(proj)
    assert len(payload["rows"]) == 10
    assert "truncated" not in payload


def test_cache_keeps_full_result(tmp_path: Path) -> None:
    """The cap is applied at response serialization — the server-side result
    cache keeps the full rows, so raising the cap doesn't require re-querying."""
    proj = _make_project(tmp_path, "title: T\ndata:\n  max_rows: 4\n")
    client = TestClient(create_app(proj))
    assert client.get("/").status_code == 200
    first = client.get("/_dashdown/api/data/all_rows?_connector=main").json()
    assert first["truncated"] is True
    # Second request hits the result cache and must still be capped + flagged.
    second = client.get("/_dashdown/api/data/all_rows?_connector=main").json()
    assert second["truncated"] is True
    assert second["total_rows"] == 10
