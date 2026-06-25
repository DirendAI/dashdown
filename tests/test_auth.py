"""Tests for built-in authentication (Stage 8a).

Two layers: unit tests for parsing + credential checks in ``dashdown.auth``,
and integration tests driving the FastAPI app through ``TestClient`` to confirm
the middleware guards real requests and exempts the health probe.
"""
import base64
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dashdown.auth import (
    AuthConfig,
    _check_api_key,
    _check_basic,
    challenge_headers,
    is_authorized,
    parse_auth_config,
)
from dashdown.server import create_app


# --------------------------------------------------------------------------- #
# parse_auth_config
# --------------------------------------------------------------------------- #
class TestParseAuthConfig:
    def test_none_when_missing(self):
        cfg = parse_auth_config(None)
        assert cfg.type == "none"
        assert cfg.enabled is False

    def test_explicit_none(self):
        cfg = parse_auth_config({"type": "none"})
        assert cfg.type == "none"
        assert cfg.enabled is False

    def test_basic_single_user(self):
        cfg = parse_auth_config({"type": "basic", "username": "admin", "password": "pw"})
        assert cfg.type == "basic"
        assert cfg.enabled is True
        assert cfg.users == {"admin": "pw"}

    def test_basic_users_mapping(self):
        cfg = parse_auth_config(
            {"type": "basic", "users": {"admin": "a", "viewer": "v"}}
        )
        assert cfg.users == {"admin": "a", "viewer": "v"}

    def test_basic_username_and_users_merge(self):
        cfg = parse_auth_config(
            {"type": "basic", "username": "root", "password": "r", "users": {"x": "y"}}
        )
        assert cfg.users == {"root": "r", "x": "y"}

    def test_basic_requires_credentials(self):
        with pytest.raises(ValueError, match="requires"):
            parse_auth_config({"type": "basic"})

    def test_basic_users_must_be_mapping(self):
        with pytest.raises(ValueError, match="mapping"):
            parse_auth_config({"type": "basic", "users": ["a", "b"]})

    def test_api_key_single(self):
        cfg = parse_auth_config({"type": "api_key", "key": "secret"})
        assert cfg.type == "api_key"
        assert cfg.header == "X-API-Key"
        assert cfg.keys == ["secret"]

    def test_api_key_custom_header_and_list(self):
        cfg = parse_auth_config(
            {"type": "api_key", "header": "X-Token", "keys": ["a", "b"]}
        )
        assert cfg.header == "X-Token"
        assert cfg.keys == ["a", "b"]

    def test_api_key_requires_key(self):
        with pytest.raises(ValueError, match="requires"):
            parse_auth_config({"type": "api_key"})

    def test_api_key_keys_must_be_list(self):
        with pytest.raises(ValueError, match="list"):
            parse_auth_config({"type": "api_key", "keys": "nope"})

    def test_unknown_type(self):
        with pytest.raises(ValueError, match="unknown auth.type"):
            parse_auth_config({"type": "oauth"})

    def test_not_a_mapping(self):
        with pytest.raises(ValueError, match="must be a mapping"):
            parse_auth_config("basic")

    def test_realm_passthrough(self):
        cfg = parse_auth_config(
            {"type": "basic", "username": "a", "password": "b", "realm": "Stats"}
        )
        assert cfg.realm == "Stats"


class TestEnvSecretResolution:
    def test_env_var_resolved(self, monkeypatch):
        monkeypatch.setenv("DASH_PW", "from-env")
        cfg = parse_auth_config({"type": "basic", "username": "a", "password": "${DASH_PW}"})
        assert cfg.users == {"a": "from-env"}

    def test_missing_env_var_raises(self, monkeypatch):
        monkeypatch.delenv("NOT_SET_PW", raising=False)
        with pytest.raises(ValueError, match="NOT_SET_PW"):
            parse_auth_config(
                {"type": "basic", "username": "a", "password": "${NOT_SET_PW}"}
            )

    def test_api_key_env_resolved(self, monkeypatch):
        monkeypatch.setenv("API_TOKEN", "tok123")
        cfg = parse_auth_config({"type": "api_key", "key": "${API_TOKEN}"})
        assert cfg.keys == ["tok123"]

    def test_literal_value_not_treated_as_env(self):
        cfg = parse_auth_config({"type": "api_key", "key": "plain-key"})
        assert cfg.keys == ["plain-key"]


# --------------------------------------------------------------------------- #
# credential checks
# --------------------------------------------------------------------------- #
def _basic_header(user: str, pw: str) -> str:
    raw = base64.b64encode(f"{user}:{pw}".encode()).decode()
    return f"Basic {raw}"


class TestCheckBasic:
    cfg = AuthConfig(type="basic", users={"admin": "pw"})

    def test_correct(self):
        assert _check_basic(self.cfg, _basic_header("admin", "pw")) is True

    def test_wrong_password(self):
        assert _check_basic(self.cfg, _basic_header("admin", "nope")) is False

    def test_unknown_user(self):
        assert _check_basic(self.cfg, _basic_header("ghost", "pw")) is False

    def test_missing_header(self):
        assert _check_basic(self.cfg, None) is False

    def test_wrong_scheme(self):
        assert _check_basic(self.cfg, "Bearer abc") is False

    def test_malformed_base64(self):
        assert _check_basic(self.cfg, "Basic not-base64!!") is False

    def test_no_colon(self):
        raw = base64.b64encode(b"adminpw").decode()
        assert _check_basic(self.cfg, f"Basic {raw}") is False

    def test_empty_password_matches_empty(self):
        cfg = AuthConfig(type="basic", users={"u": ""})
        assert _check_basic(cfg, _basic_header("u", "")) is True


class TestCheckApiKey:
    cfg = AuthConfig(type="api_key", keys=["a", "b"])

    def test_correct(self):
        assert _check_api_key(self.cfg, "a") is True
        assert _check_api_key(self.cfg, "b") is True

    def test_wrong(self):
        assert _check_api_key(self.cfg, "c") is False

    def test_missing(self):
        assert _check_api_key(self.cfg, None) is False
        assert _check_api_key(self.cfg, "") is False


class TestChallengeHeaders:
    def test_basic_challenge(self):
        h = challenge_headers(AuthConfig(type="basic", realm="My Site"))
        assert h["WWW-Authenticate"] == 'Basic realm="My Site"'

    def test_realm_quotes_stripped(self):
        h = challenge_headers(AuthConfig(type="basic", realm='ev"il'))
        assert h["WWW-Authenticate"] == 'Basic realm="evil"'

    def test_api_key_no_challenge(self):
        assert challenge_headers(AuthConfig(type="api_key", keys=["x"])) == {}


class TestIsAuthorized:
    def test_disabled_always_authorized(self):
        class _Req:
            headers = {}

        assert is_authorized(AuthConfig(type="none"), _Req()) is True


# --------------------------------------------------------------------------- #
# integration: middleware on a live app
# --------------------------------------------------------------------------- #
def _make_project(tmp: Path, auth_yaml: str = "") -> Path:
    (tmp / "pages").mkdir()
    (tmp / "pages" / "index.md").write_text("# Home\n\nHello.", encoding="utf-8")
    (tmp / "dashdown.yaml").write_text(
        "title: Test\ntheme: light\n" + auth_yaml, encoding="utf-8"
    )
    return tmp


@pytest.fixture
def tmp_project():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


class TestNoAuth:
    def test_open_by_default(self, tmp_project):
        root = _make_project(tmp_project)
        client = TestClient(create_app(root))
        assert client.get("/").status_code == 200


class TestBasicAuthMiddleware:
    AUTH = "auth:\n  type: basic\n  username: admin\n  password: s3cret\n"

    def _client(self, tmp):
        return TestClient(create_app(_make_project(tmp, self.AUTH)))

    def test_no_credentials_401(self, tmp_project):
        resp = self._client(tmp_project).get("/")
        assert resp.status_code == 401
        assert resp.headers["WWW-Authenticate"].startswith("Basic realm=")

    def test_wrong_credentials_401(self, tmp_project):
        resp = self._client(tmp_project).get("/", auth=("admin", "wrong"))
        assert resp.status_code == 401

    def test_correct_credentials_200(self, tmp_project):
        resp = self._client(tmp_project).get("/", auth=("admin", "s3cret"))
        assert resp.status_code == 200
        assert "Hello" in resp.text

    def test_health_exempt(self, tmp_project):
        resp = self._client(tmp_project).get("/_dashdown/health")
        assert resp.status_code == 200
        assert resp.text == "ok"

    def test_static_assets_guarded(self, tmp_project):
        client = self._client(tmp_project)
        assert client.get("/_dashdown/static/dashdown.js").status_code == 401
        assert (
            client.get(
                "/_dashdown/static/dashdown.js", auth=("admin", "s3cret")
            ).status_code
            == 200
        )


class TestApiKeyMiddleware:
    AUTH = "auth:\n  type: api_key\n  header: X-API-Key\n  key: tok-123\n"

    def _client(self, tmp):
        return TestClient(create_app(_make_project(tmp, self.AUTH)))

    def test_missing_key_401(self, tmp_project):
        resp = self._client(tmp_project).get("/")
        assert resp.status_code == 401
        assert "WWW-Authenticate" not in resp.headers

    def test_wrong_key_401(self, tmp_project):
        resp = self._client(tmp_project).get("/", headers={"X-API-Key": "nope"})
        assert resp.status_code == 401

    def test_correct_key_200(self, tmp_project):
        resp = self._client(tmp_project).get("/", headers={"X-API-Key": "tok-123"})
        assert resp.status_code == 200

    def test_health_exempt(self, tmp_project):
        assert self._client(tmp_project).get("/_dashdown/health").status_code == 200


class TestMisconfiguredAuthFailsLoad:
    def test_invalid_auth_raises_on_load(self, tmp_project):
        root = _make_project(tmp_project, "auth:\n  type: basic\n")
        with pytest.raises(ValueError):
            create_app(root)
