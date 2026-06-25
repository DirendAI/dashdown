"""Regression tests for dynamic `[slug]` detail-page data correctness.

A dynamic page (`pages/teams/[team].md`) serves many records from one template;
its queries substitute the route param (`${team}`). The bug this guards against:
the route value never travelled with the *data* request, so every record's data
URL was byte-identical (`/api/data/team_summary?_connector=main`) and cacheable —
the browser served the first-viewed record's data for every later one, and the
server leaned on a global, concurrency-unsafe `default_params` to disambiguate.

The fix: the page emits its route params to the client (`#dashdown-route-params`),
which carries them on every data request, and the global query cache is registered
with **empty** default params (no per-record state). These tests lock both halves.
"""
import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dashdown.render.pipeline import (
    _library_keys,
    _query_def_cache,
    _result_cache,
    _stream_def_cache,
    render_page,
)
from dashdown.server import create_app


def _clear_caches():
    _query_def_cache.clear()
    _stream_def_cache.clear()
    _library_keys.clear()
    _result_cache.clear()


def _make_project(tmp: Path) -> Path:
    (tmp / "pages" / "teams").mkdir(parents=True)
    (tmp / "pages" / "index.md").write_text("# Home\n", encoding="utf-8")
    # One template, many records: `${team}` is the captured route segment.
    (tmp / "pages" / "teams" / "[team].md").write_text(
        "# Team\n\n"
        ":::query name=team_summary connector=main\n"
        "SELECT team, wins FROM standings WHERE team = '${team}'\n"
        ":::\n\n"
        "<Table data={team_summary} />\n",
        encoding="utf-8",
    )
    (tmp / "data").mkdir()
    (tmp / "data" / "standings.csv").write_text(
        "team,wins\nQatar,3\nBrazil,9\n", encoding="utf-8"
    )
    (tmp / "sources.yaml").write_text(
        "main:\n  type: csv\n  directory: data\n", encoding="utf-8"
    )
    (tmp / "dashdown.yaml").write_text("title: Test\n", encoding="utf-8")
    return tmp


@pytest.fixture
def project(tmp_path):
    _clear_caches()
    yield _make_project(tmp_path)
    _clear_caches()


# --------------------------------------------------------------------------- #
# render_page captures the route params
# --------------------------------------------------------------------------- #
class TestRouteParamsCaptured:
    def test_render_page_exposes_route_params(self):
        page = render_page(
            "# T\n\n:::query name=q connector=main\n"
            "SELECT * FROM t WHERE id = '${id}'\n:::\n\n<Table data={q} />\n",
            {},
            params={"id": "42"},
        )
        assert page.route_params == {"id": "42"}

    def test_static_page_has_no_route_params(self):
        page = render_page("# T\n\nNo params here.\n", {})
        assert page.route_params == {}

    def test_global_cache_holds_no_route_params(self):
        """The registered query def must carry empty default params — the route
        value travels with the request, never the global cache (concurrency)."""
        _clear_caches()
        render_page(
            "# T\n\n:::query name=q connector=main\n"
            "SELECT * FROM t WHERE id = '${id}'\n:::\n\n<Table data={q} />\n",
            {},
            params={"id": "42"},
        )
        _sql, default_params, _ttl = _query_def_cache[("q", "main")]
        assert default_params == {}


# --------------------------------------------------------------------------- #
# end-to-end: the browser sequence that exposed the bug
# --------------------------------------------------------------------------- #
class TestDataApiPerRecord:
    def _route_params(self, html: str) -> dict:
        marker = '<script id="dashdown-route-params" type="application/json">'
        start = html.index(marker) + len(marker)
        end = html.index("</script>", start)
        return json.loads(html[start:end])

    def test_page_emits_route_params_to_client(self, project):
        client = TestClient(create_app(project))
        assert self._route_params(client.get("/teams/Qatar").text) == {"team": "Qatar"}
        assert self._route_params(client.get("/teams/Brazil").text) == {"team": "Brazil"}

    def test_data_api_returns_the_requested_record(self, project):
        client = TestClient(create_app(project))
        client.get("/teams/Qatar")  # render registers the query def
        qatar = client.get(
            "/_dashdown/api/data/team_summary?team=Qatar&_connector=main"
        ).json()
        assert qatar["rows"] == [["Qatar", 3]]
        brazil = client.get(
            "/_dashdown/api/data/team_summary?team=Brazil&_connector=main"
        ).json()
        assert brazil["rows"] == [["Brazil", 9]]

    def test_no_cross_contamination_after_other_slug_rendered(self, project):
        """Render Brazil *last*, then ask for Qatar's data — the response must
        follow the request param, not whatever page was rendered most recently."""
        client = TestClient(create_app(project))
        client.get("/teams/Qatar")
        client.get("/teams/Brazil")  # last render — old code's global said "Brazil"
        qatar = client.get(
            "/_dashdown/api/data/team_summary?team=Qatar&_connector=main"
        ).json()
        assert qatar["rows"] == [["Qatar", 3]]  # not Brazil's row

    def test_param_less_request_does_not_leak_a_record(self, project):
        """A request with no route param must NOT return some record's data from
        the global cache (the old cross-contamination vector): with empty default
        params, `${team}` -> '' matches nothing. On the buggy code this returned
        the last-rendered team's row."""
        client = TestClient(create_app(project))
        client.get("/teams/Qatar")
        client.get("/teams/Brazil")
        resp = client.get("/_dashdown/api/data/team_summary?_connector=main")
        assert resp.json()["rows"] == []

    def test_per_record_urls_are_distinct_and_cacheable(self, project):
        """Each record now has a unique data URL (carries its route param), so the
        max-age cache can't serve one record's response for another."""
        client = TestClient(create_app(project))
        client.get("/teams/Qatar")
        r1 = client.get("/_dashdown/api/data/team_summary?team=Qatar&_connector=main")
        r2 = client.get("/_dashdown/api/data/team_summary?team=Brazil&_connector=main")
        # Cache-Control is still present (caching stays on)...
        assert "max-age" in r1.headers.get("cache-control", "")
        # ...but the two records' payloads differ, and their URLs differ too.
        assert r1.json()["rows"] != r2.json()["rows"]
