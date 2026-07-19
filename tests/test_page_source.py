"""Tests for the page-source editing surface — GET/PUT /_dashdown/api/page-source.

The dev-server-only source-editing endpoints let the client read a page's raw
markdown (with a content fingerprint) and write it back under optimistic
concurrency (a stale token → 409). Section edits/deletes are done client-side by
splicing between the kept-answer markers (:func:`find_kept_sections`) and PUTting
the whole file — the server exposes no per-section endpoints.

Shares the shape of tests/test_ask_keep.py (CSV source + a `by_region` library
query) so the keep endpoint has a real artifact to reference.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dashdown import ask_engine
from dashdown.ask_engine import find_kept_sections
from dashdown.render import pipeline
from dashdown.server import create_app


@pytest.fixture(autouse=True)
def _clear_caches():
    """Def caches are module-global; isolate every test (mirrors test_ask_keep)."""

    def _clear():
        ask_engine._answer_cache.clear()
        ask_engine._rate_marks.clear()
        pipeline._query_def_cache.clear()
        pipeline._result_cache.clear()
        pipeline._python_def_cache.clear()
        pipeline._stream_def_cache.clear()
        pipeline._library_keys.clear()
        pipeline._python_library_keys.clear()

    _clear()
    yield
    _clear()


def _make_project(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "pages").mkdir()
    (root / "data").mkdir()
    (root / "queries").mkdir()
    (root / "dashdown.yaml").write_text(
        "title: Page Source Test\n", encoding="utf-8"
    )
    (root / "sources.yaml").write_text(
        "main:\n  type: csv\n  directory: data\n", encoding="utf-8"
    )
    (root / "data" / "sales.csv").write_text(
        "region,amount\nNorth,100\nSouth,200\n", encoding="utf-8"
    )
    (root / "queries" / "by_region.sql").write_text(
        "---\ndescription: Revenue by region\n---\n"
        "SELECT region, SUM(amount) AS total FROM sales GROUP BY region\n",
        encoding="utf-8",
    )
    (root / "pages" / "index.md").write_text(
        "# Home\n\nWelcome.\n", encoding="utf-8"
    )
    return root


@pytest.fixture
def proj(tmp_path):
    return _make_project(tmp_path / "proj")


def _client(root: Path, *, dev: bool = True) -> TestClient:
    return TestClient(create_app(root, dev=dev))


# --------------------------------------------------------------------------- #
# GET /_dashdown/api/page-source
# --------------------------------------------------------------------------- #
class TestGetPageSource:
    def test_happy_and_token_stable(self, proj):
        client = _client(proj)
        r = client.get("/_dashdown/api/page-source", params={"path": "/"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["path"] == "/"
        assert body["markdown"] == (proj / "pages" / "index.md").read_text(
            encoding="utf-8"
        )
        assert body["token"] == hashlib.sha1(
            body["markdown"].encode("utf-8")
        ).hexdigest()
        # A second identical read yields the same fingerprint (content-based).
        r2 = client.get("/_dashdown/api/page-source", params={"path": "/"})
        assert r2.json()["token"] == body["token"]

    def test_non_dev_is_403(self, proj):
        client = _client(proj, dev=False)
        r = client.get("/_dashdown/api/page-source", params={"path": "/"})
        assert r.status_code == 403

    def test_unknown_page_is_404(self, proj):
        client = _client(proj)
        r = client.get("/_dashdown/api/page-source", params={"path": "/nope"})
        assert r.status_code == 404

    def test_missing_path_is_400(self, proj):
        client = _client(proj)
        r = client.get("/_dashdown/api/page-source")
        assert r.status_code == 400

    def test_dynamic_slug_is_400(self, proj):
        (proj / "pages" / "[id].md").write_text("# Item\n", encoding="utf-8")
        client = _client(proj)
        r = client.get("/_dashdown/api/page-source", params={"path": "/anything"})
        assert r.status_code == 400

    def test_traversal_is_404(self, proj):
        client = _client(proj)
        r = client.get("/_dashdown/api/page-source", params={"path": "../.."})
        assert r.status_code == 404


# --------------------------------------------------------------------------- #
# PUT /_dashdown/api/page-source
# --------------------------------------------------------------------------- #
class TestPutPageSource:
    def _token(self, client: TestClient, path: str = "/") -> str:
        return client.get(
            "/_dashdown/api/page-source", params={"path": path}
        ).json()["token"]

    def test_roundtrip_persists_and_returns_new_token(self, proj):
        client = _client(proj)
        token = self._token(client)
        new_md = "# Home\n\nEdited body.\n"
        r = client.put(
            "/_dashdown/api/page-source",
            json={"path": "/", "markdown": new_md, "token": token},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["token"] == hashlib.sha1(new_md.encode("utf-8")).hexdigest()
        # Persisted to disk, and a fresh GET reports the same new token.
        assert (proj / "pages" / "index.md").read_text(encoding="utf-8") == new_md
        assert self._token(client) == body["token"]

    def test_stale_token_is_409_with_current(self, proj):
        client = _client(proj)
        current = self._token(client)
        r = client.put(
            "/_dashdown/api/page-source",
            json={"path": "/", "markdown": "# X\n", "token": "0" * 40},
        )
        assert r.status_code == 409
        body = r.json()
        assert body["token"] == current
        assert "detail" in body
        # The file is untouched by a refused write.
        assert "# Home" in (proj / "pages" / "index.md").read_text(encoding="utf-8")
        # ...and the current token then lets the write through.
        r2 = client.put(
            "/_dashdown/api/page-source",
            json={"path": "/", "markdown": "# X\n", "token": body["token"]},
        )
        assert r2.status_code == 200

    def test_non_object_body_is_400(self, proj):
        client = _client(proj)
        assert client.put("/_dashdown/api/page-source", json="hi").status_code == 400

    def test_non_string_markdown_is_400(self, proj):
        client = _client(proj)
        token = self._token(client)
        r = client.put(
            "/_dashdown/api/page-source",
            json={"path": "/", "markdown": 123, "token": token},
        )
        assert r.status_code == 400

    def test_missing_path_is_400(self, proj):
        client = _client(proj)
        token = self._token(client)
        r = client.put(
            "/_dashdown/api/page-source",
            json={"markdown": "# X\n", "token": token},
        )
        assert r.status_code == 400

    def test_oversize_is_400(self, proj):
        client = _client(proj)
        token = self._token(client)
        big = "a" * (2 * 1024 * 1024 + 1)
        r = client.put(
            "/_dashdown/api/page-source",
            json={"path": "/", "markdown": big, "token": token},
        )
        assert r.status_code == 400

    def test_non_dev_is_403(self, proj):
        client = _client(proj, dev=False)
        r = client.put(
            "/_dashdown/api/page-source",
            json={"path": "/", "markdown": "# X\n", "token": "x"},
        )
        assert r.status_code == 403

    def test_unknown_page_is_404(self, proj):
        client = _client(proj)
        r = client.put(
            "/_dashdown/api/page-source",
            json={"path": "/nope", "markdown": "# X\n", "token": "x"},
        )
        assert r.status_code == 404


# --------------------------------------------------------------------------- #
# Keep → GET → splice-delete → PUT (the client's section-op flow)
# --------------------------------------------------------------------------- #
class TestKeepThenSpliceDelete:
    def _keep(self, client: TestClient, question: str) -> dict:
        r = client.post(
            "/_dashdown/api/ask/keep",
            json={
                "question": question,
                "resolved": {"kind": "query", "detail": {"name": "by_region"}},
                "chart": {"type": "bar", "x": "region", "y": "total"},
                "path": "/",
            },
        )
        assert r.status_code == 200, r.text
        return r.json()

    def test_keep_response_carries_id_and_token_and_markers(self, proj):
        client = _client(proj)
        page = proj / "pages" / "index.md"
        body = self._keep(client, "revenue by region")
        keep_id = body["id"]
        assert len(keep_id) == 8
        content = page.read_text(encoding="utf-8")
        assert f"<!-- dashdown:keep id={keep_id} kind=query · " in content
        assert f"<!-- /dashdown:keep id={keep_id} -->" in content
        # The keep response token matches the file's fingerprint (so the client can
        # immediately PUT edits against it without a re-GET).
        assert body["token"] == hashlib.sha1(content.encode("utf-8")).hexdigest()

    def test_get_splice_delete_put_leaves_valid_markdown(self, proj):
        client = _client(proj)
        page = proj / "pages" / "index.md"
        # Keep two sections, then delete the first by splicing its marker span.
        first = self._keep(client, "first kept answer")
        self._keep(client, "second kept answer")

        got = client.get("/_dashdown/api/page-source", params={"path": "/"}).json()
        md, token = got["markdown"], got["token"]
        sections = find_kept_sections(md)
        assert len(sections) == 2
        target = next(s for s in sections if s.id == first["id"])
        # Splice the section out by its marker span and tidy the seam (what the
        # client does after find_kept_sections).
        spliced = re.sub(
            r"\n{3,}", "\n\n", md[: target.start] + md[target.end :]
        )

        r = client.put(
            "/_dashdown/api/page-source",
            json={"path": "/", "markdown": spliced, "token": token},
        )
        assert r.status_code == 200, r.text

        after = page.read_text(encoding="utf-8")
        remaining = find_kept_sections(after)
        assert len(remaining) == 1
        assert remaining[0].id != first["id"]
        assert "first kept answer" not in after
        assert "second kept answer" in after
        assert "\n\n\n" not in after
