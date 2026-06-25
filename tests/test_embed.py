"""Tests for embeddable pages (Stage 12).

Covers the pure layer — ``embed:`` config parsing, framing headers, and signed
embed-token crypto — plus ``TestClient`` integration driving the FastAPI app to
confirm the chrome-less render, deny-by-default framing, and that a signed,
page-scoped token unlocks exactly its page + that page's queries when ``auth``
is enabled (and nothing else)."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dashdown.embed import (
    EmbedConfig,
    frame_headers,
    parse_embed_config,
    query_key,
    sign_embed_token,
    token_allows_query,
    verify_embed_token,
)
from dashdown.server import create_app


# --------------------------------------------------------------------------- #
# config parsing
# --------------------------------------------------------------------------- #
class TestParseEmbedConfig:
    def test_none_disabled_default(self):
        cfg = parse_embed_config(None)
        assert cfg.enabled is False
        assert cfg.frame_ancestors == []
        assert cfg.secret is None
        assert cfg.has_secret is False

    def test_enabled_with_allowlist(self):
        cfg = parse_embed_config(
            {"enabled": True, "frame_ancestors": ["https://notion.so", "https://a.example:8443"]}
        )
        assert cfg.enabled is True
        assert cfg.frame_ancestors == ["https://notion.so", "https://a.example:8443"]

    def test_frame_ancestors_single_string(self):
        cfg = parse_embed_config({"enabled": True, "frame_ancestors": "https://x.example"})
        assert cfg.frame_ancestors == ["https://x.example"]

    def test_wildcard_origin_allowed(self):
        cfg = parse_embed_config({"frame_ancestors": "*"})
        assert cfg.frame_ancestors == ["*"]

    def test_invalid_origin_raises(self):
        with pytest.raises(ValueError):
            parse_embed_config({"frame_ancestors": ["notanorigin"]})

    def test_origin_with_path_raises(self):
        with pytest.raises(ValueError):
            parse_embed_config({"frame_ancestors": ["https://x.example/path"]})

    def test_not_a_mapping_raises(self):
        with pytest.raises(ValueError):
            parse_embed_config(["https://x.example"])

    def test_secret_env_expansion(self, monkeypatch):
        monkeypatch.setenv("EMB_SECRET", "shh")
        cfg = parse_embed_config({"secret": "${EMB_SECRET}"})
        assert cfg.secret == "shh"
        assert cfg.has_secret is True

    def test_missing_env_secret_raises(self, monkeypatch):
        monkeypatch.delenv("EMB_MISSING", raising=False)
        with pytest.raises(ValueError):
            parse_embed_config({"secret": "${EMB_MISSING}"})

    def test_token_ttl_validation(self):
        assert parse_embed_config({"token_ttl": 60}).token_ttl == 60
        with pytest.raises(ValueError):
            parse_embed_config({"token_ttl": -1})
        with pytest.raises(ValueError):
            parse_embed_config({"token_ttl": "soon"})


# --------------------------------------------------------------------------- #
# framing headers
# --------------------------------------------------------------------------- #
class TestFrameHeaders:
    def test_deny_by_default(self):
        assert frame_headers(EmbedConfig(enabled=True)) == {"X-Frame-Options": "DENY"}
        assert frame_headers(EmbedConfig(enabled=False)) == {"X-Frame-Options": "DENY"}

    def test_csp_when_allowlisted(self):
        cfg = EmbedConfig(enabled=True, frame_ancestors=["https://a.example", "https://b.example"])
        assert frame_headers(cfg) == {
            "Content-Security-Policy": "frame-ancestors https://a.example https://b.example"
        }

    def test_allowlist_ignored_when_disabled(self):
        cfg = EmbedConfig(enabled=False, frame_ancestors=["https://a.example"])
        assert frame_headers(cfg) == {"X-Frame-Options": "DENY"}


# --------------------------------------------------------------------------- #
# signed tokens
# --------------------------------------------------------------------------- #
class TestEmbedTokens:
    SECRET = "test-secret-key"

    def test_round_trip(self):
        tok = sign_embed_token(self.SECRET, "/sales", ["main:q1", "main:q2"])
        payload = verify_embed_token(self.SECRET, tok)
        assert payload is not None
        assert payload["path"] == "/sales"
        assert payload["q"] == ["main:q1", "main:q2"]

    def test_tamper_rejected(self):
        tok = sign_embed_token(self.SECRET, "/sales", ["main:q1"])
        body, _, sig = tok.partition(".")
        forged = verify_embed_token(
            self.SECRET,
            # Swap in a payload claiming a different page, keep the old signature.
            sign_embed_token(self.SECRET, "/secret", ["main:q1"]).split(".")[0] + "." + sig,
        )
        assert forged is None

    def test_wrong_secret_rejected(self):
        tok = sign_embed_token(self.SECRET, "/sales", ["main:q1"])
        assert verify_embed_token("other-secret", tok) is None

    def test_expiry_rejected(self):
        tok = sign_embed_token(self.SECRET, "/sales", ["main:q1"], exp=1000)
        assert verify_embed_token(self.SECRET, tok, now=999) is not None
        assert verify_embed_token(self.SECRET, tok, now=1000) is None
        assert verify_embed_token(self.SECRET, tok, now=2000) is None

    def test_malformed_rejected(self):
        assert verify_embed_token(self.SECRET, None) is None
        assert verify_embed_token(self.SECRET, "") is None
        assert verify_embed_token(self.SECRET, "nodot") is None
        assert verify_embed_token(self.SECRET, "bad.payload") is None
        assert verify_embed_token("", "a.b") is None

    def test_token_allows_query(self):
        payload = verify_embed_token(self.SECRET, sign_embed_token(self.SECRET, "/p", ["main:q1"]))
        assert token_allows_query(payload, "main", "q1") is True
        assert token_allows_query(payload, "main", "q2") is False
        assert token_allows_query(payload, "other", "q1") is False

    def test_query_key(self):
        assert query_key("main", "q1") == "main:q1"


# --------------------------------------------------------------------------- #
# integration helpers
# --------------------------------------------------------------------------- #
def _make_project(tmp: Path, extra_yaml: str = "") -> Path:
    (tmp / "pages").mkdir()
    (tmp / "pages" / "index.md").write_text("# Home\n\nWelcome home.", encoding="utf-8")
    (tmp / "pages" / "sales.md").write_text(
        "# Sales\n\n"
        ":::query name=q1 connector=main\nSELECT * FROM sales\n:::\n\n"
        "<Table data={q1} />\n",
        encoding="utf-8",
    )
    (tmp / "data").mkdir()
    (tmp / "data" / "sales.csv").write_text("region,amount\nNorth,10\nSouth,20\n", encoding="utf-8")
    (tmp / "sources.yaml").write_text("main:\n  type: csv\n  directory: data\n", encoding="utf-8")
    (tmp / "dashdown.yaml").write_text(
        "title: Test\ntheme: light\n" + extra_yaml, encoding="utf-8"
    )
    return tmp


@pytest.fixture
def tmp_project():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


_EMBED_ON = "embed:\n  enabled: true\n  frame_ancestors:\n    - https://host.example\n"


# --------------------------------------------------------------------------- #
# integration: chrome-less render + framing (no auth)
# --------------------------------------------------------------------------- #
class TestEmbedRender:
    def test_normal_render_has_chrome(self, tmp_project):
        client = TestClient(create_app(_make_project(tmp_project, _EMBED_ON)))
        html = client.get("/sales").text
        assert "dashdown-header" in html
        assert "dashdown-sidebar" in html
        assert "Sales" in html  # body content present

    def test_embed_render_omits_chrome(self, tmp_project):
        client = TestClient(create_app(_make_project(tmp_project, _EMBED_ON)))
        html = client.get("/sales?_embed=1").text
        assert "dashdown-header" not in html
        assert "dashdown-sidenav" not in html  # nav tree not shipped
        assert "Sales" in html  # body content still rendered

    def test_embed_ignored_when_disabled(self, tmp_project):
        # No embed block → embedding off → ?_embed renders the full shell.
        client = TestClient(create_app(_make_project(tmp_project)))
        html = client.get("/sales?_embed=1").text
        assert "dashdown-header" in html

    def test_framing_csp_when_allowlisted(self, tmp_project):
        client = TestClient(create_app(_make_project(tmp_project, _EMBED_ON)))
        resp = client.get("/sales")
        assert resp.headers["content-security-policy"] == "frame-ancestors https://host.example"
        assert "x-frame-options" not in resp.headers

    def test_framing_denied_without_allowlist(self, tmp_project):
        client = TestClient(create_app(_make_project(tmp_project, "embed:\n  enabled: true\n")))
        resp = client.get("/sales")
        assert resp.headers["x-frame-options"] == "DENY"

    def test_framing_denied_when_no_embed_block(self, tmp_project):
        client = TestClient(create_app(_make_project(tmp_project)))
        resp = client.get("/sales")
        assert resp.headers["x-frame-options"] == "DENY"


# --------------------------------------------------------------------------- #
# integration: signed tokens under auth
# --------------------------------------------------------------------------- #
_AUTH = "auth:\n  type: basic\n  username: admin\n  password: s3cret\n"
_EMBED_TOKENS = (
    "embed:\n  enabled: true\n  secret: topsecret\n"
    "  frame_ancestors:\n    - https://host.example\n"
)


class TestEmbedTokensIntegration:
    YAML = _AUTH + _EMBED_TOKENS

    def _app(self, tmp):
        return create_app(_make_project(tmp, self.YAML))

    def _mint(self, client, path="/sales"):
        resp = client.get(
            "/_dashdown/api/embed-token?path=" + path, auth=("admin", "s3cret")
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["token"]

    def test_mint_requires_auth(self, tmp_project):
        client = TestClient(self._app(tmp_project))
        assert client.get("/_dashdown/api/embed-token?path=/sales").status_code == 401

    def test_mint_returns_scoped_token(self, tmp_project):
        client = TestClient(self._app(tmp_project))
        data = client.get(
            "/_dashdown/api/embed-token?path=/sales", auth=("admin", "s3cret")
        ).json()
        assert data["path"] == "/sales"
        assert "main:q1" in data["queries"]

    def test_no_token_page_401(self, tmp_project):
        client = TestClient(self._app(tmp_project))
        assert client.get("/sales?_embed=1").status_code == 401

    def test_valid_token_renders_chromeless(self, tmp_project):
        client = TestClient(self._app(tmp_project))
        token = self._mint(client)
        resp = client.get("/sales?_embed=" + token)
        assert resp.status_code == 200
        assert "dashdown-header" not in resp.text
        assert "Sales" in resp.text

    def test_token_scoped_to_its_page(self, tmp_project):
        client = TestClient(self._app(tmp_project))
        token = self._mint(client, path="/sales")
        # The /sales token must not unlock the / (index) page.
        assert client.get("/?_embed=" + token).status_code == 401

    def test_tampered_token_401(self, tmp_project):
        client = TestClient(self._app(tmp_project))
        token = self._mint(client)
        assert client.get("/sales?_embed=" + token + "x").status_code == 401

    def test_data_api_in_scope_allowed(self, tmp_project):
        client = TestClient(self._app(tmp_project))
        token = self._mint(client)  # also registers q1 (renders the page)
        resp = client.get("/_dashdown/api/data/q1?_connector=main&_embed=" + token)
        assert resp.status_code == 200
        assert resp.json()["columns"] == ["region", "amount"]

    def test_data_api_out_of_scope_401(self, tmp_project):
        client = TestClient(self._app(tmp_project))
        token = self._mint(client)
        resp = client.get("/_dashdown/api/data/other?_connector=main&_embed=" + token)
        assert resp.status_code == 401

    def test_static_assets_allowed_with_token(self, tmp_project):
        client = TestClient(self._app(tmp_project))
        token = self._mint(client)
        resp = client.get("/_dashdown/static/dashdown.js?_embed=" + token)
        assert resp.status_code == 200
        # Without any token, the asset stays guarded.
        assert client.get("/_dashdown/static/dashdown.js").status_code == 401
