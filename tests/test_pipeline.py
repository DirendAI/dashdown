"""Tests for dashdown.render.pipeline module."""
import time

import pytest

from dashdown.render.pipeline import (
    _substitute_params,
    _expand_in_list,
    build_options_sql,
    MAX_OPTIONS_LIMIT,
    MAX_IN_VALUES,
    register_query_def,
    get_query_def,
    _query_def_cache,
    _result_cache,
    get_cached_result,
    cache_result,
    DEFAULT_CACHE_TTL,
    MAX_CACHED_RESULTS,
    RenderedPage,
    render_page,
    serialize_value,
    serialize_result,
)
from dashdown.data.base import QueryResult


class TestSubstituteParams:
    """Tests for _substitute_params function - SQL injection prevention."""

    def test_basic_string_placeholder_in_quotes(self):
        """Placeholder inside quotes should escape internal quotes."""
        sql = "WHERE name = '${name}'"
        params = {"name": "O'Reilly"}
        result = _substitute_params(sql, params)
        assert result == "WHERE name = 'O''Reilly'"

    def test_basic_string_placeholder_not_in_quotes(self):
        """Placeholder NOT in quotes should wrap value in quotes and escape."""
        sql = "WHERE id = ${id}"
        params = {"id": "123"}
        result = _substitute_params(sql, params)
        assert result == "WHERE id = '123'"

    def test_sql_injection_attempt_in_quoted_context(self):
        """SQL injection in quoted context should be escaped to literal string."""
        sql = "WHERE name = '${name}'"
        params = {"name": "1 OR 1=1"}
        result = _substitute_params(sql, params)
        assert result == "WHERE name = '1 OR 1=1'"

    def test_sql_injection_attempt_in_unquoted_context(self):
        """SQL injection in unquoted context should be wrapped in quotes."""
        sql = "WHERE id = ${id}"
        params = {"id": "1 OR 1=1"}
        result = _substitute_params(sql, params)
        assert result == "WHERE id = '1 OR 1=1'"

    def test_multiple_placeholders(self):
        """Multiple placeholders should all be replaced."""
        sql = "WHERE name = '${name}' AND id = ${id}"
        params = {"name": "John", "id": "42"}
        result = _substitute_params(sql, params)
        assert result == "WHERE name = 'John' AND id = '42'"

    def test_missing_param_defaults_to_empty_string(self):
        """Missing params should default to empty string."""
        sql = "WHERE name = '${name}'"
        params = {}
        result = _substitute_params(sql, params)
        assert result == "WHERE name = ''"

    def test_param_with_quotes_in_unquoted_context(self):
        """Quotes in param value should be escaped even in unquoted placeholder."""
        sql = "WHERE name = ${name}"
        params = {"name": "O'Reilly"}
        result = _substitute_params(sql, params)
        assert result == "WHERE name = 'O''Reilly'"

    def test_numeric_value_as_string(self):
        """Numeric-looking values are treated as strings (DuckDB auto-casts)."""
        sql = "WHERE amount = ${amount}"
        params = {"amount": "1000.50"}
        result = _substitute_params(sql, params)
        assert result == "WHERE amount = '1000.50'"

    def test_empty_string_param(self):
        """Empty string params should work."""
        sql = "WHERE name = '${name}'"
        params = {"name": ""}
        result = _substitute_params(sql, params)
        assert result == "WHERE name = ''"

    def test_no_placeholders(self):
        """SQL without placeholders should be unchanged."""
        sql = "SELECT * FROM users"
        params = {"name": "John"}
        result = _substitute_params(sql, params)
        assert result == "SELECT * FROM users"

    def test_placeholder_with_special_chars(self):
        """Special characters in params should be preserved."""
        sql = "WHERE name = '${name}'"
        params = {"name": "test@example.com"}
        result = _substitute_params(sql, params)
        assert result == "WHERE name = 'test@example.com'"

    def test_non_string_param_value(self):
        """Non-string param values should be converted to string."""
        sql = "WHERE id = ${id}"
        params = {"id": 123}
        result = _substitute_params(sql, params)
        assert result == "WHERE id = '123'"

    def test_complex_sql_with_multiple_clauses(self):
        """Complex SQL with multiple WHERE clauses."""
        sql = """
        SELECT * FROM users
        WHERE name = '${name}'
        AND status = ${status}
        AND created_at > '${date}'
        """
        params = {"name": "John", "status": "active", "date": "2024-01-01"}
        result = _substitute_params(sql, params)
        expected = """
        SELECT * FROM users
        WHERE name = 'John'
        AND status = 'active'
        AND created_at > '2024-01-01'
        """.strip()
        assert result.strip() == expected

    def test_double_quoted_placeholder(self):
        """Placeholder inside double quotes (DAX string literal) substitutes in place."""
        sql = 'VAR SelectedType = "${product_type}"'
        params = {"product_type": "Sales Inventory"}
        result = _substitute_params(sql, params)
        assert result == 'VAR SelectedType = "Sales Inventory"'

    def test_double_quoted_placeholder_empty_value(self):
        """Empty value in double quotes must yield an empty DAX string literal."""
        sql = 'VAR SelectedType = "${product_type}"'
        params = {}
        result = _substitute_params(sql, params)
        assert result == 'VAR SelectedType = ""'

    def test_double_quoted_placeholder_escapes_double_quotes(self):
        """Double quotes in the value are doubled so they can't break out."""
        sql = 'VAR v = "${val}"'
        params = {"val": 'say "hi"'}
        result = _substitute_params(sql, params)
        assert result == 'VAR v = "say ""hi"""'

    def test_injection_attempt_in_double_quoted_context(self):
        """Breakout attempt in double-quoted context stays inside the literal."""
        sql = 'VAR v = "${val}"'
        params = {"val": '" OR 1=1 --'}
        result = _substitute_params(sql, params)
        # value's one " doubles to ""; with the SQL's own opening delimiter the
        # parser sees an escaped quote inside the literal, not a breakout
        assert result == 'VAR v = """ OR 1=1 --"'

    def test_single_quotes_in_double_quoted_context_untouched(self):
        """Single quotes are harmless inside a double-quoted literal — no doubling."""
        sql = 'VAR v = "${val}"'
        params = {"val": "O'Reilly"}
        result = _substitute_params(sql, params)
        assert result == 'VAR v = "O\'Reilly"'

    def test_mixed_quote_contexts(self):
        """Single-, double-, and unquoted placeholders each escape per context."""
        sql = "WHERE a = '${x}' AND b = \"${x}\" AND c = ${x}"
        params = {"x": "v'al"}
        result = _substitute_params(sql, params)
        assert result == "WHERE a = 'v''al' AND b = \"v'al\" AND c = 'v''al'"


class TestInListExpansion:
    """Multi-select `IN (...)` expansion in _substitute_params.

    A `${param}` that is the whole content of an `IN (...)` list expands a
    comma-separated value into a quoted, per-item-escaped literal list. Every
    item still goes through the Stage-1 single-quote escaping, so the injection
    guarantees are unchanged per value.
    """

    def test_basic_in_list(self):
        sql = "WHERE region IN (${region})"
        result = _substitute_params(sql, {"region": "East,West"})
        assert result == "WHERE region IN ('East', 'West')"

    def test_single_value_in_list(self):
        """One value is still a valid 1-element list."""
        sql = "WHERE region IN (${region})"
        result = _substitute_params(sql, {"region": "East"})
        assert result == "WHERE region IN ('East')"

    def test_empty_value_becomes_null(self):
        """Nothing selected -> IN (NULL): valid SQL that matches nothing, so the
        author's `'${x}' = '' OR ...` all-guard stays syntactically valid."""
        sql = "WHERE region IN (${region})"
        assert _substitute_params(sql, {"region": ""}) == "WHERE region IN (NULL)"
        assert _substitute_params(sql, {}) == "WHERE region IN (NULL)"

    def test_whitespace_trimmed_and_empties_dropped(self):
        sql = "WHERE r IN (${r})"
        result = _substitute_params(sql, {"r": " A , B ,, C "})
        assert result == "WHERE r IN ('A', 'B', 'C')"

    def test_per_item_quote_escaping(self):
        """Each value is single-quote-escaped, so a quote can't break out."""
        sql = "WHERE name IN (${name})"
        result = _substitute_params(sql, {"name": "O'Reilly,Smith"})
        assert result == "WHERE name IN ('O''Reilly', 'Smith')"

    def test_injection_attempt_per_item(self):
        sql = "WHERE id IN (${id})"
        result = _substitute_params(sql, {"id": "1' OR '1'='1,2"})
        # Each comma-split fragment is a single quoted literal with its quotes
        # doubled — the `'` can't break out of the list, it stays inert data.
        assert result == "WHERE id IN ('1'' OR ''1''=''1', '2')"

    def test_not_in_also_expands(self):
        sql = "WHERE region NOT IN (${region})"
        result = _substitute_params(sql, {"region": "a,b"})
        assert result == "WHERE region NOT IN ('a', 'b')"

    def test_case_insensitive_and_no_space(self):
        assert _substitute_params("x in(${r})", {"r": "a,b"}) == "x in('a', 'b')"
        assert (
            _substitute_params("x IN  (  ${r}  )", {"r": "a,b"})
            == "x IN  (  'a', 'b'  )"
        )

    def test_join_is_not_in(self):
        """`JOIN (...)` must not be mistaken for an `IN (...)` list."""
        sql = "FROM t JOIN (${j})"
        result = _substitute_params(sql, {"j": "a,b"})
        # bare placeholder -> wrapped as one literal, comma kept
        assert result == "FROM t JOIN ('a,b')"

    def test_equality_context_keeps_comma_literal(self):
        """A non-IN bare placeholder is unchanged: comma stays inside one literal
        (single-select equality is never split)."""
        sql = "WHERE region = ${region}"
        result = _substitute_params(sql, {"region": "East,West"})
        assert result == "WHERE region = 'East,West'"

    def test_quoted_context_not_split(self):
        """A quoted `'${x}'` is the all-guard literal, never an IN list."""
        sql = "WHERE '${region}' = '' OR region IN (${region})"
        result = _substitute_params(sql, {"region": "East,West"})
        assert result == "WHERE 'East,West' = '' OR region IN ('East', 'West')"

    def test_all_guard_empty(self):
        sql = "WHERE '${region}' = '' OR region IN (${region})"
        result = _substitute_params(sql, {"region": ""})
        assert result == "WHERE '' = '' OR region IN (NULL)"

    def test_length_capped(self):
        many = ",".join(str(i) for i in range(MAX_IN_VALUES + 50))
        out = _expand_in_list(many)
        assert out.count("'") == MAX_IN_VALUES * 2  # two quotes per item, capped

    def test_expand_helper_empty(self):
        assert _expand_in_list("") == "NULL"
        assert _expand_in_list("  ,  , ") == "NULL"


class TestBuildOptionsSql:
    """The <Combobox> distinct-values wrap — the only new SQL surface the
    searchable filter adds. Locks its injection-safety (column whitelist +
    search escaping) and shape."""

    INNER = "SELECT * FROM customers WHERE region = 'East'"

    def test_basic_shape(self):
        out = build_options_sql(self.INNER, "name")
        assert 'SELECT DISTINCT CAST("name" AS VARCHAR) AS value' in out
        assert f"FROM (\n{self.INNER}\n) AS _dd_opt" in out
        assert '"name" IS NOT NULL' in out
        assert "ORDER BY value" in out
        assert out.rstrip().endswith("LIMIT 50")  # default

    def test_search_is_escaped_and_added(self):
        out = build_options_sql(self.INNER, "name", "Smith")
        assert "ILIKE '%' || 'Smith' || '%'" in out

    def test_search_ranks_prefix_matches_first(self):
        # Typing "num" must surface "numpy" above "abnum": prefix matches sort
        # ahead of substring matches, case-insensitively alphabetical within each
        # band (so "numpy" also beats "NumPyX", and an exact match comes first).
        out = build_options_sql(self.INNER, "name", "num")
        assert (
            "ORDER BY CASE WHEN value ILIKE 'num' || '%' THEN 0 ELSE 1 END, LOWER(value), value"
            in out
        )
        # The ranking layer wraps the DISTINCT (Postgres rejects a DISTINCT
        # query ordered by an expression outside its select list).
        assert ") AS _dd_vals" in out
        import duckdb

        rows = duckdb.sql(
            build_options_sql(
                "SELECT * FROM (VALUES ('abnum'), ('NumPyX'), ('numpy'), ('numba'), ('zeta')) t(name)",
                "name",
                "num",
            )
        ).fetchall()
        assert [r[0] for r in rows] == ["numba", "numpy", "NumPyX", "abnum"]

    def test_no_search_keeps_single_layer_shape(self):
        out = build_options_sql(self.INNER, "name")
        assert "_dd_vals" not in out
        assert "ORDER BY value" in out

    def test_search_single_quote_is_doubled(self):
        # A crafted search term can only ever be a quoted string literal.
        out = build_options_sql(self.INNER, "name", "O'Reilly")
        assert "'O''Reilly'" in out
        # The raw, unescaped single quote never appears as a lone literal break.
        assert "'O'Reilly'" not in out

    def test_search_injection_attempt_is_inert(self):
        out = build_options_sql(self.INNER, "name", "x' OR '1'='1")
        # Doubled quotes neutralize the break-out attempt.
        assert "'x'' OR ''1''=''1'" in out

    @pytest.mark.parametrize(
        "bad",
        [
            'name"; DROP TABLE users; --',
            "name OR 1=1",
            "name; DELETE",
            "(SELECT 1)",
            "a-b",
            "a b",
            "",
            "1name",
            "name)",
        ],
    )
    def test_invalid_column_raises(self, bad):
        with pytest.raises(ValueError):
            build_options_sql(self.INNER, bad)

    @pytest.mark.parametrize("good", ["name", "_id", "Region", "col_2", "a"])
    def test_valid_columns_accepted(self, good):
        out = build_options_sql(self.INNER, good)
        assert f'"{good}"' in out

    def test_limit_is_clamped(self):
        assert build_options_sql(self.INNER, "name", limit=99999).rstrip().endswith(
            f"LIMIT {MAX_OPTIONS_LIMIT}"
        )
        assert build_options_sql(self.INNER, "name", limit=0).rstrip().endswith("LIMIT 1")
        assert build_options_sql(self.INNER, "name", limit="bad").rstrip().endswith("LIMIT 50")

    def test_trailing_semicolon_stripped(self):
        out = build_options_sql("SELECT * FROM t;", "name")
        assert "FROM (\nSELECT * FROM t\n) AS _dd_opt" in out


class TestQueryDefCache:
    """Tests for query definition caching."""

    def setup_method(self):
        """Clear cache before each test."""
        _query_def_cache.clear()

    def teardown_method(self):
        """Clear cache after each test."""
        _query_def_cache.clear()

    def test_register_and_get_query_def(self):
        """Query definitions can be registered and retrieved."""
        register_query_def("test_query", "main", "SELECT * FROM test", {})
        result = get_query_def("test_query", "main")
        assert result is not None
        sql, params, cache_ttl = result
        assert sql == "SELECT * FROM test"
        assert params == {}
        assert cache_ttl is None

    def test_register_with_cache_ttl(self):
        """Query definitions can be registered with a cache_ttl."""
        register_query_def("test_query", "main", "SELECT 1", {}, cache_ttl=300)
        sql, params, cache_ttl = get_query_def("test_query", "main")
        assert cache_ttl == 300

    def test_get_nonexistent_query_def(self):
        """Getting a non-existent query def returns None."""
        result = get_query_def("nonexistent", "main")
        assert result is None

    def test_query_defs_isolated_by_connector(self):
        """Query defs are isolated by connector name."""
        register_query_def("my_query", "connector_a", "SELECT * FROM a", {})
        register_query_def("my_query", "connector_b", "SELECT * FROM b", {})

        sql_a, _, _ = get_query_def("my_query", "connector_a")
        sql_b, _, _ = get_query_def("my_query", "connector_b")

        assert sql_a == "SELECT * FROM a"
        assert sql_b == "SELECT * FROM b"

    def test_register_overwrites_existing(self):
        """Re-registering a query def overwrites the previous one."""
        register_query_def("test", "main", "SELECT * FROM v1", {})
        register_query_def("test", "main", "SELECT * FROM v2", {})

        sql, _, _ = get_query_def("test", "main")
        assert sql == "SELECT * FROM v2"

    def test_cache_persists_across_calls(self):
        """Cache persists across multiple register/get calls."""
        register_query_def("q1", "main", "SQL1", {})
        register_query_def("q2", "main", "SQL2", {})

        sql1, _, _ = get_query_def("q1", "main")
        sql2, _, _ = get_query_def("q2", "main")
        assert sql1 == "SQL1"
        assert sql2 == "SQL2"


class TestRenderedPage:
    """Tests for RenderedPage dataclass."""

    def test_rendered_page_defaults(self):
        """RenderedPage has correct default values."""
        page = RenderedPage(body_html="<html></html>", datasets={}, errors=[])
        assert page.body_html == "<html></html>"
        assert page.datasets == {}
        assert page.errors == []
        assert page.frontmatter == {}
        assert page.query_defs == {}

    def test_rendered_page_with_all_fields(self):
        """RenderedPage can be created with all fields."""
        page = RenderedPage(
            body_html="<html></html>",
            datasets={"q1": {"columns": ["id"], "rows": [[1]]}},
            errors=["Error 1"],
            frontmatter={"title": "Test"},
            query_defs={"q1": {"connector": "main", "sql": "SELECT 1"}},
        )
        assert page.body_html == "<html></html>"
        assert page.datasets == {"q1": {"columns": ["id"], "rows": [[1]]}}
        assert page.errors == ["Error 1"]
        assert page.frontmatter == {"title": "Test"}
        assert page.query_defs == {"q1": {"connector": "main", "sql": "SELECT 1"}}


_QUERY_SOURCE = """\
:::query name=sales connector=main
SELECT * FROM sales
:::
"""


class TestRenderPageOmitsSql:
    """Query SQL is never emitted into client query_defs — it stays server-side."""

    def setup_method(self):
        _query_def_cache.clear()

    def teardown_method(self):
        _query_def_cache.clear()

    def test_sql_omitted_from_query_defs(self):
        """render_page never ships query SQL to the client; only the connector does."""
        page = render_page(_QUERY_SOURCE, {})
        assert "sales" in page.query_defs
        assert "sql" not in page.query_defs["sales"]
        assert page.query_defs["sales"]["connector"] == "main"


_QUERY_WITH_TTL = """\
:::query name=sales connector=main cache_ttl=300
SELECT * FROM sales
:::
"""

_QUERY_NO_TTL = """\
:::query name=totals connector=main
SELECT COUNT(*) FROM orders
:::
"""


class TestCacheTtlInQueryDefs:
    """Tests for cache_ttl flowing through render_page into query_defs."""

    def setup_method(self):
        _query_def_cache.clear()

    def teardown_method(self):
        _query_def_cache.clear()

    def test_cache_ttl_emitted_when_set(self):
        """cache_ttl appears in query_defs when set on the :::query block."""
        page = render_page(_QUERY_WITH_TTL, {})
        assert page.query_defs["sales"]["cache_ttl"] == 300

    def test_cache_ttl_absent_when_not_set(self):
        """cache_ttl is not emitted in query_defs when not specified."""
        page = render_page(_QUERY_NO_TTL, {})
        assert "cache_ttl" not in page.query_defs["totals"]

    def test_cache_ttl_stored_in_query_def_cache(self):
        """cache_ttl is stored in the query def cache."""
        render_page(_QUERY_WITH_TTL, {})
        _, _, cache_ttl = get_query_def("sales", "main")
        assert cache_ttl == 300

    def test_no_cache_ttl_stored_as_none(self):
        """Queries without cache_ttl store None in the query def cache."""
        render_page(_QUERY_NO_TTL, {})
        _, _, cache_ttl = get_query_def("totals", "main")
        assert cache_ttl is None


_QUERY_WITH_PARAMS = """\
:::query name=orders connector=main
SELECT * FROM orders
WHERE region = '${region}'
  AND order_date BETWEEN '${date_start}' AND '${date_end}'
  AND region = '${region}'
:::
"""

_QUERY_NO_PARAMS = """\
:::query name=totals connector=main
SELECT COUNT(*) FROM orders
:::
"""


class TestParamsInQueryDefs:
    """Stage 19: per-query `${param}` names are surfaced into client query_defs
    (for the per-widget "filtered by" indicator) — names only, never the SQL."""

    def setup_method(self):
        _query_def_cache.clear()

    def teardown_method(self):
        _query_def_cache.clear()

    def test_params_emitted_sorted_and_deduped(self):
        """A query's distinct placeholder names are emitted, sorted & deduped."""
        page = render_page(_QUERY_WITH_PARAMS, {})
        assert page.query_defs["orders"]["params"] == [
            "date_end",
            "date_start",
            "region",
        ]

    def test_params_empty_list_when_no_placeholders(self):
        """A SQL query with no placeholders advertises a known, empty param set
        (so the client knows it never reacts to filters)."""
        page = render_page(_QUERY_NO_PARAMS, {})
        assert page.query_defs["totals"]["params"] == []

    def test_params_never_leak_sql(self):
        """Surfacing param names must not start shipping the SQL."""
        page = render_page(_QUERY_WITH_PARAMS, {})
        assert "sql" not in page.query_defs["orders"]

    def test_python_query_marked_params_unknown(self):
        """A Python library query has no SQL to scan — its filter usage isn't
        statically knowable, so it advertises `params_unknown` instead."""
        from dashdown.python_query import query as py_query
        from tests.test_python_query import parse_python_query_file_inline
        from dashdown.render.pipeline import (
            register_python_library_queries,
            _python_def_cache,
            _python_library_keys,
        )

        @py_query(connector="main")
        def churn(params, connect):
            return []

        spec = parse_python_query_file_inline(churn, "churn")
        _python_def_cache.clear()
        _python_library_keys.clear()
        register_python_library_queries({"churn": spec})
        try:
            page = render_page(
                "<Table data={churn} />",
                {},
                python_library={"churn": spec},
            )
            assert page.query_defs["churn"]["params_unknown"] is True
            assert "params" not in page.query_defs["churn"]
            assert "sql" not in page.query_defs["churn"]
        finally:
            _python_def_cache.clear()
            _python_library_keys.clear()


class TestResultCache:
    """Tests for the server-side query result cache."""

    def setup_method(self):
        _result_cache.clear()

    def teardown_method(self):
        _result_cache.clear()

    def _make_result(self):
        return QueryResult(columns=["id", "name"], rows=[[1, "Alice"], [2, "Bob"]])

    def test_miss_returns_none(self):
        """Cache miss returns None."""
        assert get_cached_result("q", "main", {}) is None

    def test_store_and_retrieve(self):
        """Stored result can be retrieved within TTL."""
        result = self._make_result()
        cache_result("q", "main", {}, result, ttl=60)
        cached = get_cached_result("q", "main", {})
        assert cached is not None
        assert cached.columns == ["id", "name"]
        assert cached.rows == [[1, "Alice"], [2, "Bob"]]

    def test_expired_entry_returns_none(self):
        """Entries past their TTL are evicted and return None."""
        result = self._make_result()
        cache_result("q", "main", {}, result, ttl=0)
        # ttl=0 means expiry = now; sleep a tiny bit to ensure it's past
        time.sleep(0.01)
        assert get_cached_result("q", "main", {}) is None

    def test_params_are_part_of_cache_key(self):
        """Different params produce different cache entries."""
        r1 = QueryResult(columns=["v"], rows=[[1]])
        r2 = QueryResult(columns=["v"], rows=[[2]])
        cache_result("q", "main", {"region": "East"}, r1, ttl=60)
        cache_result("q", "main", {"region": "West"}, r2, ttl=60)

        assert get_cached_result("q", "main", {"region": "East"}).rows == [[1]]
        assert get_cached_result("q", "main", {"region": "West"}).rows == [[2]]

    def test_params_order_independent(self):
        """Cache key is the same regardless of param insertion order."""
        result = self._make_result()
        cache_result("q", "main", {"a": "1", "b": "2"}, result, ttl=60)
        assert get_cached_result("q", "main", {"b": "2", "a": "1"}) is not None

    def test_default_cache_ttl_is_sixty(self):
        """DEFAULT_CACHE_TTL is 60 seconds."""
        assert DEFAULT_CACHE_TTL == 60

    def test_size_is_bounded(self):
        """Inserting past MAX_CACHED_RESULTS evicts rather than grows."""
        result = self._make_result()
        for i in range(MAX_CACHED_RESULTS + 50):
            cache_result("q", "main", {"id": str(i)}, result, ttl=60)
        assert len(_result_cache) == MAX_CACHED_RESULTS

    def test_eviction_is_least_recently_used(self):
        """When full, the least-recently-READ entry goes first, not the oldest-written."""
        result = self._make_result()
        for i in range(MAX_CACHED_RESULTS):
            cache_result("q", "main", {"id": str(i)}, result, ttl=60)
        # Touch the oldest-written entry, promoting it over id=1.
        assert get_cached_result("q", "main", {"id": "0"}) is not None
        cache_result("q", "main", {"id": "overflow"}, result, ttl=60)
        assert get_cached_result("q", "main", {"id": "0"}) is not None
        assert get_cached_result("q", "main", {"id": "1"}) is None

    def test_overwrite_refreshes_lru_position(self):
        """Re-caching an existing key moves it to the fresh end."""
        result = self._make_result()
        for i in range(MAX_CACHED_RESULTS):
            cache_result("q", "main", {"id": str(i)}, result, ttl=60)
        cache_result("q", "main", {"id": "0"}, result, ttl=60)
        cache_result("q", "main", {"id": "overflow"}, result, ttl=60)
        assert get_cached_result("q", "main", {"id": "0"}) is not None
        assert get_cached_result("q", "main", {"id": "1"}) is None


class TestPageHeader:
    """Tests for the page-header injection (description subtitle + updated stamp)."""

    def setup_method(self):
        _query_def_cache.clear()

    def teardown_method(self):
        _query_def_cache.clear()

    def test_description_renders_subtitle_under_h1(self):
        src = "---\ntitle: T\ndescription: Sales at a glance\n---\n\n# Heading\n\nBody.\n"
        html = render_page(src, {}).body_html
        assert '<div class="dashdown-page-header">' in html
        assert '<p class="dashdown-page-description">Sales at a glance</p>' in html
        # The H1 is preserved, and the subtitle follows it.
        assert "<h1>Heading</h1>" in html
        assert html.index("<h1>Heading</h1>") < html.index("Sales at a glance")

    def test_description_is_html_escaped(self):
        src = '---\ndescription: "A & B <x>"\n---\n\n# H\n'
        html = render_page(src, {}).body_html
        assert "A &amp; B &lt;x&gt;" in html
        assert "<x>" not in html

    def test_no_description_or_updated_leaves_body_untouched(self):
        src = "---\ntitle: T\n---\n\n# Heading\n\nBody.\n"
        html = render_page(src, {}).body_html
        assert "dashdown-page-header" not in html
        assert "<h1>Heading</h1>" in html

    def test_updated_true_renders_auto_stamp(self):
        src = "---\nupdated: true\n---\n\n# H\n"
        html = render_page(src, {}).body_html
        assert "data-dashdown-updated" in html
        assert '<span class="dashdown-updated-time">' in html
        # Auto stamp carries no literal time text server-side.
        assert ">Updated <span" in html

    def test_updated_literal_renders_verbatim(self):
        src = '---\nupdated: "Q2 2026"\n---\n\n# H\n'
        html = render_page(src, {}).body_html
        assert "data-dashdown-updated" not in html
        assert '<span class="dashdown-page-updated">Updated Q2 2026</span>' in html

    def test_updated_false_renders_no_stamp(self):
        src = "---\nupdated: false\n---\n\n# H\n"
        html = render_page(src, {}).body_html
        assert "dashdown-page-updated" not in html
        assert "dashdown-page-header" not in html

    def test_header_prepended_when_no_h1(self):
        src = "---\ndescription: Just a subtitle\n---\n\nBody only, no heading.\n"
        html = render_page(src, {}).body_html
        assert html.startswith('<div class="dashdown-page-header">')
        assert "Just a subtitle" in html


class TestFilterBarSlot:
    """Tests for the filter-row slot grafted below the page header (#17).

    Placement default flipped: filter controls render **inline where authored**
    by default, so the slot is emitted only when a control opts INTO the top bar
    (`bar` / `filter_bar=true`). A page of purely inline controls — or no
    controls — gets no slot at all. The slot lives inside body_html so it travels
    with the page content (e.g. if a page is ever embedded without the app chrome).
    """

    # Inline by default (no slot); opts into the bar (slot emitted).
    FILTER = '<Dropdown name="region" options="a,b" label="Region" />'
    BAR_FILTER = '<Dropdown name="region" options="a,b" label="Region" bar />'

    def setup_method(self):
        _query_def_cache.clear()

    def teardown_method(self):
        _query_def_cache.clear()

    def test_slot_emitted_after_h1_when_page_has_bar_filter(self):
        src = f"# Heading\n\n{self.BAR_FILTER}\n\nBody.\n"
        html = render_page(src, {}).body_html
        assert 'id="dashdown-filter-bar-container"' in html
        assert 'id="dashdown-filter-bar-search"' in html
        # Directly after the title, before the content (and the control's own
        # placeholder, which filter_bar.js relocates into the slot).
        assert html.index("<h1>Heading</h1>") < html.index("dashdown-filter-bar-container")
        assert html.index("dashdown-filter-bar-container") < html.index("Body.")

    def test_slot_sits_below_grafted_page_header(self):
        src = f"---\ndescription: Sub\n---\n\n# Heading\n\n{self.BAR_FILTER}\n"
        html = render_page(src, {}).body_html
        header_end = html.index("</div>", html.index("dashdown-page-header"))
        assert html.index("dashdown-filter-bar-container") > header_end

    def test_no_slot_without_filters(self):
        src = "# Heading\n\nJust text.\n"
        html = render_page(src, {}).body_html
        assert "dashdown-filter-bar" not in html

    def test_inline_filter_renders_without_slot(self):
        # The new default: a bare filter renders in place, no top bar chrome.
        src = f"# Heading\n\n{self.FILTER}\n\nBody.\n"
        html = render_page(src, {}).body_html
        assert "dashdown-dropdown" in html  # the control itself is rendered
        assert "dashdown-filter-bar" not in html  # but no bar slot
        assert "data-filter-bar" not in html  # and no routing marker

    def test_no_slot_in_static_build(self):
        # Static builds strip filter controls, so the slot is omitted too.
        src = f"# Heading\n\n{self.BAR_FILTER}\n"
        html = render_page(src, {}, static_build=True).body_html
        assert "dashdown-filter-bar" not in html
        assert "dashdown-dropdown" not in html

    def test_slot_prepended_when_no_h1(self):
        src = f"{self.BAR_FILTER}\n\nBody only.\n"
        html = render_page(src, {}).body_html
        assert html.index("dashdown-filter-bar-container") < html.index("Body only.")

    def test_bar_attribute_emits_routing_marker(self):
        # `bar` (and legacy `filter_bar=true`) route a control to the top bar.
        for src in (
            '# H\n\n<Dropdown name="r" options="a,b" label="R" bar />\n',
            '# H\n\n<Dropdown name="r" options="a,b" label="R" filter_bar=true />\n',
        ):
            html = render_page(src, {}).body_html
            assert 'data-filter-bar="true"' in html
            assert "dashdown-filter-bar-container" in html  # slot emitted

    def test_inline_emits_no_marker(self):
        # Default and legacy `filter_bar=false` both stay inline: no marker, no slot.
        for src in (
            f"# H\n\n{self.FILTER}\n",
            '# H\n\n<Dropdown name="r" options="a,b" label="R" filter_bar=false />\n',
        ):
            html = render_page(src, {}).body_html
            assert "data-filter-bar" not in html
            assert "dashdown-filter-bar" not in html

    def test_slot_includes_drawer_and_button(self):
        # The slot ships the drawer surface alongside the inline row (#21):
        # the body slot filter_bar.js routes overflow controls into, and the
        # (initially hidden) trigger button at the end of the row.
        src = f"# Heading\n\n{self.BAR_FILTER}\n"
        html = render_page(src, {}).body_html
        assert 'id="dashdown-filter-drawer-body"' in html
        # Hidden until filter_bar.js routes a control into the drawer.
        assert 'id="dashdown-filter-drawer-btn" hidden' in html

    def test_filter_mode_defaults_to_auto(self):
        src = f"# Heading\n\n{self.BAR_FILTER}\n"
        html = render_page(src, {}).body_html
        assert 'data-filter-mode="auto"' in html

    def test_filters_drawer_frontmatter_forces_drawer_mode(self):
        src = f"---\nfilters: drawer\n---\n\n# Heading\n\n{self.BAR_FILTER}\n"
        html = render_page(src, {}).body_html
        assert 'data-filter-mode="drawer"' in html

    def test_unknown_filters_frontmatter_falls_back_to_auto(self):
        src = f"---\nfilters: sidebar\n---\n\n# Heading\n\n{self.BAR_FILTER}\n"
        html = render_page(src, {}).body_html
        assert 'data-filter-mode="auto"' in html


class TestSerializeValue:
    """`serialize_value` is the single coercion seam before JSON for both the
    live data API and the static build, so every driver-native type a connector
    can return must survive it."""

    def test_decimal_becomes_float(self):
        # Regression: DuckDB-backed connectors return DECIMAL columns (and any
        # `SUM(x) * 0.12`-style arithmetic) as Python Decimal, which is not
        # JSON-serializable — the data API 500'd before this coercion existed.
        from decimal import Decimal

        out = serialize_value(Decimal("1285.20"))
        assert out == pytest.approx(1285.20)
        assert isinstance(out, float)

    def test_decimal_nan_becomes_none(self):
        from decimal import Decimal

        assert serialize_value(Decimal("NaN")) is None

    def test_serialize_result_with_decimal_is_json_safe(self):
        import json
        from decimal import Decimal

        result = QueryResult(columns=["downloads", "revenue"], rows=[[10710, Decimal("1285.20")]])
        payload = serialize_result(result)
        # Must not raise — this is exactly what the data API does.
        json.dumps(payload)
        assert payload["rows"][0][1] == pytest.approx(1285.20)
