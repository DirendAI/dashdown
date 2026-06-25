"""Tests for the pluggable semantic-backend registry (Stage 18c).

Dependency-free: exercises the registry, entry-point discovery, the missing-dep
hint, `claims_connector` detection, and that the pipeline dispatch calls **only**
`introspect` + `build_spec` — all with fake backends/entry points, no BSL/Ibis/DAX.

Importing `dashdown.semantic` eagerly registers the built-in `ibis`/`cube` backends;
an autouse fixture snapshots + restores the module-global registry so a test's fake
backends never leak into the next test (or into test_semantic*.py).
"""
from __future__ import annotations

import pytest

import dashdown.semantic  # noqa: F401 — eager-registers IbisBackend + CubeBackend
import dashdown.semantic_base as sb
from dashdown.semantic import (
    SemanticModelHandle,
    SemanticRef,
    _detect_backend,
    _introspect,
    build_semantic_spec,
)
from dashdown.semantic_base import (
    SemanticBackend,
    detect_backend,
    get_semantic_backend,
    known_semantic_backends,
    register_semantic_backend,
)


@pytest.fixture(autouse=True)
def _isolate_registry():
    """Snapshot + restore the registry globals so fake backends don't leak."""
    reg = dict(sb._REGISTRY)
    eps = sb._ENTRY_POINTS
    yield
    sb._REGISTRY.clear()
    sb._REGISTRY.update(reg)
    sb._ENTRY_POINTS = eps


class _FakeEP:
    """A stand-in for importlib.metadata.EntryPoint."""

    def __init__(self, name, value, loader):
        self.name = name
        self.value = value
        self._loader = loader

    def load(self):
        return self._loader()


def _noop_backend(name="fake"):
    @register_semantic_backend(name)
    class _B(SemanticBackend):
        def introspect(self, handle, connectors):  # pragma: no cover - trivial
            pass

        def build_spec(self, handle, ref, connectors):  # pragma: no cover - trivial
            pass

    return _B


# --------------------------------------------------------------------------- #
# Built-ins + basic registry
# --------------------------------------------------------------------------- #


def test_builtins_registered_eagerly():
    known = known_semantic_backends()
    assert "ibis" in known and "cube" in known
    assert type(get_semantic_backend("ibis")).__name__ == "IbisBackend"
    assert type(get_semantic_backend("cube")).__name__ == "CubeBackend"


def test_unknown_backend_raises():
    with pytest.raises(KeyError):
        get_semantic_backend("does-not-exist")


def test_register_decorator_adds_singleton():
    _noop_backend("regtest")
    assert get_semantic_backend("regtest").name == "regtest"
    # singleton — same instance each lookup
    assert get_semantic_backend("regtest") is get_semantic_backend("regtest")
    assert "regtest" in known_semantic_backends()


# --------------------------------------------------------------------------- #
# claims_connector + detection
# --------------------------------------------------------------------------- #


class CubeConnector:  # the exact __name__ claims_connector keys off
    pass


class _CSVConnector:
    pass


def test_claims_connector():
    assert get_semantic_backend("cube").claims_connector(CubeConnector()) is True
    assert get_semantic_backend("cube").claims_connector(_CSVConnector()) is False
    assert get_semantic_backend("ibis").claims_connector(CubeConnector()) is False


def test_detect_backend_explicit_and_inference():
    # Rename the stub so its __name__ is exactly "CubeConnector".
    CubeConnector = type("CubeConnector", (), {})
    assert detect_backend("cube", None) == "cube"
    assert detect_backend("IBIS", None) == "ibis"           # case-insensitive
    assert detect_backend(None, CubeConnector()) == "cube"  # inferred
    assert detect_backend(None, _CSVConnector()) == "ibis"  # fallback
    assert detect_backend(None, None) == "ibis"
    with pytest.raises(ValueError):
        detect_backend("nope", None)


def test_detect_backend_skips_a_broken_probe():
    @register_semantic_backend("boom")
    class _Boom(SemanticBackend):
        def claims_connector(self, conn):
            raise RuntimeError("probe blew up")

        def introspect(self, handle, connectors):  # pragma: no cover
            pass

        def build_spec(self, handle, ref, connectors):  # pragma: no cover
            pass

    # A backend whose probe raises must not break detection — falls back to ibis.
    assert detect_backend(None, object()) == "ibis"


def test_wrapper_detect_backend_matches_signature():
    # The semantic._detect_backend wrapper keeps the (explicit, connector, connectors)
    # shape the loader/tests use, delegating to the registry.
    assert _detect_backend("cube", "cubesrc", {}) == "cube"
    assert _detect_backend(None, "cubesrc", {"cubesrc": CubeConnector()}) == "cube"
    assert _detect_backend(None, "missing", {}) == "ibis"


# --------------------------------------------------------------------------- #
# Entry-point discovery
# --------------------------------------------------------------------------- #


def test_get_semantic_backend_loads_entry_point(monkeypatch):
    class PluginBackend(SemanticBackend):
        def introspect(self, handle, connectors):  # pragma: no cover
            pass

        def build_spec(self, handle, ref, connectors):  # pragma: no cover
            pass

    monkeypatch.setattr(
        sb, "_ENTRY_POINTS",
        {"plugin": _FakeEP("plugin", "pkg:PluginBackend", lambda: PluginBackend)},
    )
    be = get_semantic_backend("plugin")
    assert isinstance(be, PluginBackend)
    assert be.name == "plugin"
    assert "plugin" in known_semantic_backends()
    # cached after first load (same singleton)
    assert get_semantic_backend("plugin") is be


def test_entry_point_must_be_a_subclass(monkeypatch):
    monkeypatch.setattr(
        sb, "_ENTRY_POINTS", {"bad": _FakeEP("bad", "pkg:NotABackend", lambda: object)}
    )
    with pytest.raises(TypeError):
        get_semantic_backend("bad")


def test_entry_points_dedup_first_wins(monkeypatch):
    monkeypatch.setattr(sb, "_ENTRY_POINTS", None)  # force re-discovery
    dupes = [_FakeEP("dup", "first:A", None), _FakeEP("dup", "second:B", None)]
    monkeypatch.setattr(
        sb.metadata, "entry_points",
        lambda **kw: dupes if kw.get("group") == sb.ENTRY_POINT_GROUP else [],
    )
    found = sb._entry_points()
    assert found["dup"].value == "first:A"  # first registration wins


# --------------------------------------------------------------------------- #
# Missing-dependency hint (the critic's name!=extra fix)
# --------------------------------------------------------------------------- #


def _raise_import_error():
    raise ImportError("No module named 'boring_semantic_layer'")


def test_missing_dep_hint_uses_extra_map_not_name(monkeypatch):
    # A built-in whose deps are missing must point at the RIGHT extra: ibis ->
    # dashdown-md[semantic], never the wrong dashdown-md[ibis].
    monkeypatch.setattr(sb, "_ENTRY_POINTS",
                        {"ibis": _FakeEP("ibis", "x:Y", _raise_import_error)})
    monkeypatch.delitem(sb._REGISTRY, "ibis")  # force the entry-point load path
    with pytest.raises(ImportError) as exc:
        get_semantic_backend("ibis")
    msg = str(exc.value)
    assert "dashdown-md[semantic]" in msg
    assert "dashdown-md[ibis]" not in msg


def test_missing_dep_hint_generic_for_third_party(monkeypatch):
    monkeypatch.setattr(sb, "_ENTRY_POINTS",
                        {"weird": _FakeEP("weird", "x:Y", _raise_import_error)})
    with pytest.raises(ImportError) as exc:
        get_semantic_backend("weird")
    msg = str(exc.value)
    assert "dependencies are not" in msg
    assert "dashdown-md[weird]" not in msg  # don't guess a wrong extra


# --------------------------------------------------------------------------- #
# The dispatch contract: pipeline calls ONLY introspect + build_spec
# --------------------------------------------------------------------------- #


def test_pipeline_dispatch_uses_only_the_two_methods():
    calls: list[tuple[str, str]] = []

    @register_semantic_backend("recording")
    class _Recording(SemanticBackend):
        def introspect(self, handle, connectors):
            calls.append(("introspect", handle.name))
            handle.measures = {"m"}

        def build_spec(self, handle, ref, connectors):
            calls.append(("build_spec", ref.query_name))
            return "SPEC-SENTINEL"

    handle = SemanticModelHandle(
        name="x", connector="c", file_config={}, table_connectors={},
        profile=None, profile_path=None, backend="recording",
    )
    _introspect(handle, {})
    ref = SemanticRef(model="x", metrics=("m",), by=None, connector="c",
                      query_name="_sem.x.m")
    spec = build_semantic_spec({"x": handle}, ref, {})

    assert spec == "SPEC-SENTINEL"
    assert ("introspect", "x") in calls
    assert ("build_spec", "_sem.x.m") in calls
    assert handle.measures == {"m"}  # introspect mutated the shared catalogue
