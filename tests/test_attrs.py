"""Tests for dashdown.render.attrs module."""
import pytest

from dashdown.render.attrs import parse_attrs, DataRef, _coerce


class TestDataRef:
    """Tests for DataRef dataclass."""

    def test_data_ref_equality(self):
        """DataRef instances with same name are equal."""
        ref1 = DataRef("query1")
        ref2 = DataRef("query1")
        assert ref1 == ref2

    def test_data_ref_inequality(self):
        """DataRef instances with different names are not equal."""
        ref1 = DataRef("query1")
        ref2 = DataRef("query2")
        assert ref1 != ref2

    def test_data_ref_is_frozen(self):
        """DataRef is immutable (frozen dataclass)."""
        ref = DataRef("query1")
        with pytest.raises(AttributeError):
            ref.name = "query2"


# Helper to add leading space as expected by parse_attrs
# The actual implementation prepends a space: parse_attrs(" " + after)
def _attrs(s: str) -> str:
    """Add leading space for parse_attrs compatibility."""
    return " " + s


class TestParseAttrs:
    """Tests for parse_attrs function."""

    def test_empty_string(self):
        """Empty attribute string returns empty dict."""
        result = parse_attrs("")
        assert result == {}

    def test_double_quoted_value(self):
        """Double-quoted attribute values are parsed."""
        result = parse_attrs(_attrs('title="My Title"'))
        assert result == {"title": "My Title"}

    def test_single_quoted_value(self):
        """Single-quoted attribute values are parsed."""
        result = parse_attrs(_attrs("title='My Title'"))
        assert result == {"title": "My Title"}

    def test_bareword_value(self):
        """Bareword attribute values are parsed."""
        result = parse_attrs(_attrs("count=5"))
        assert result == {"count": 5}

    def test_data_ref_value(self):
        """Data reference values {name} are parsed as DataRef."""
        result = parse_attrs(_attrs("data={my_query}"))
        assert result == {"data": DataRef("my_query")}

    def test_data_ref_with_spaces(self):
        """Data reference with spaces around name."""
        result = parse_attrs(_attrs("data={ my_query }"))
        assert result == {"data": DataRef("my_query")}

    def test_bare_flag(self):
        """Bare attribute flag (no value) sets value to True."""
        result = parse_attrs(_attrs("disabled"))
        assert result == {"disabled": True}

    def test_multiple_attributes(self):
        """Multiple attributes are parsed correctly."""
        result = parse_attrs(_attrs('title="Test" count=10 data={query} hidden'))
        assert result == {
            "title": "Test",
            "count": 10,
            "data": DataRef("query"),
            "hidden": True,
        }

    def test_quotes_in_string(self):
        """Quotes inside string values are preserved."""
        result = parse_attrs(_attrs('label="It\'s a test"'))
        assert result == {"label": "It's a test"}

    def test_double_quotes_inside_single_quoted(self):
        """Double quotes inside single-quoted string."""
        result = parse_attrs(_attrs("label='He said \"hello\"'"))
        assert result == {"label": 'He said "hello"'}

    def test_boolean_true(self):
        """Boolean true values are parsed."""
        result = parse_attrs(_attrs("enabled=true"))
        assert result == {"enabled": True}

    def test_boolean_false(self):
        """Boolean false values are parsed."""
        result = parse_attrs(_attrs("enabled=false"))
        assert result == {"enabled": False}

    def test_case_insensitive_boolean(self):
        """Boolean values are case-insensitive."""
        result = parse_attrs(_attrs("enabled=TRUE"))
        assert result == {"enabled": True}
        result = parse_attrs(_attrs("enabled=FALSE"))
        assert result == {"enabled": False}
        result = parse_attrs(_attrs("enabled=True"))
        assert result == {"enabled": True}

    def test_integer_parsing(self):
        """Integer values are parsed."""
        result = parse_attrs(_attrs("limit=100"))
        assert result == {"limit": 100}

    def test_negative_integer(self):
        """Negative integer values are parsed."""
        result = parse_attrs(_attrs("offset=-10"))
        assert result == {"offset": -10}

    def test_float_parsing(self):
        """Float values are parsed."""
        result = parse_attrs(_attrs("rate=3.14"))
        assert result == {"rate": 3.14}

    def test_negative_float(self):
        """Negative float values are parsed."""
        result = parse_attrs(_attrs("temp=-5.5"))
        assert result == {"temp": -5.5}

    def test_string_that_looks_like_number(self):
        """Strings that don't look like numbers are preserved as strings."""
        result = parse_attrs(_attrs("id=abc123"))
        assert result == {"id": "abc123"}

    def test_mixed_attributes(self):
        """Mix of different attribute types."""
        attrs = _attrs('name="test" count=5 data={query} enabled=true hidden')
        result = parse_attrs(attrs)
        assert result == {
            "name": "test",
            "count": 5,
            "data": DataRef("query"),
            "enabled": True,
            "hidden": True,
        }

    def test_attribute_with_dashes(self):
        """Attribute names can contain dashes."""
        result = parse_attrs(_attrs("data-source={query}"))
        assert result == {"data-source": DataRef("query")}

    def test_attribute_with_underscores(self):
        """Attribute names can contain underscores."""
        result = parse_attrs(_attrs("my_attr=value"))
        assert result == {"my_attr": "value"}

    def test_whitespace_handling(self):
        """Extra whitespace is handled correctly."""
        result = parse_attrs(_attrs('  title="Test"   count=5  '))
        assert result == {"title": "Test", "count": 5}

    def test_empty_quoted_value(self):
        """Empty quoted value."""
        result = parse_attrs(_attrs('label=""'))
        assert result == {"label": ""}

    def test_empty_single_quoted_value(self):
        """Empty single-quoted value."""
        result = parse_attrs(_attrs("label=''"))
        assert result == {"label": ""}


class TestCoerce:
    """Tests for _coerce helper function."""

    def test_coerce_true(self):
        """'true' string coerces to True."""
        assert _coerce("true") is True

    def test_coerce_FALSE(self):
        """'FALSE' string coerces to False."""
        assert _coerce("FALSE") is False

    def test_coerce_integer(self):
        """Integer string coerces to int."""
        assert _coerce("42") == 42

    def test_coerce_negative_integer(self):
        """Negative integer string coerces to int."""
        assert _coerce("-42") == -42

    def test_coerce_float(self):
        """Float string coerces to float."""
        assert _coerce("3.14") == 3.14

    def test_coerce_negative_float(self):
        """Negative float string coerces to float."""
        assert _coerce("-3.14") == -3.14

    def test_coerce_plain_string(self):
        """Non-numeric, non-boolean string stays as string."""
        assert _coerce("hello") == "hello"

    def test_coerce_empty_string(self):
        """Empty string stays as empty string."""
        assert _coerce("") == ""

    def test_coerce_string_with_letters_and_numbers(self):
        """Mixed string stays as string."""
        assert _coerce("abc123") == "abc123"


class TestEdgeCases:
    """Edge case tests for attribute parsing."""

    def test_equals_in_quoted_value(self):
        """Equals sign inside quoted value is preserved."""
        result = parse_attrs(_attrs('filter="key=value"'))
        assert result == {"filter": "key=value"}

    def test_spaces_in_quoted_value(self):
        """Spaces inside quoted value are preserved."""
        result = parse_attrs(_attrs('label="Hello World"'))
        assert result == {"label": "Hello World"}

    def test_data_ref_with_underscores(self):
        """DataRef can have underscores in name."""
        result = parse_attrs(_attrs("data={my_query_name}"))
        assert result == {"data": DataRef("my_query_name")}

    def test_data_ref_with_numbers(self):
        """DataRef can have numbers in name."""
        result = parse_attrs(_attrs("data={query123}"))
        assert result == {"data": DataRef("query123")}


class TestArrayLiteral:
    """`key={[...]}` parses to a Python list (not a DataRef)."""

    def test_int_list(self):
        result = parse_attrs(_attrs("default={[0, 10000]}"))
        assert result == {"default": [0, 10000]}

    def test_no_spaces(self):
        result = parse_attrs(_attrs("default={[1,2,3]}"))
        assert result == {"default": [1, 2, 3]}

    def test_float_and_mixed(self):
        result = parse_attrs(_attrs("range={[0.5, 2.5]}"))
        assert result == {"range": [0.5, 2.5]}

    def test_string_list_json(self):
        result = parse_attrs(_attrs('items={["a", "b"]}'))
        assert result == {"items": ["a", "b"]}

    def test_single_quoted_items_fall_back_to_split(self):
        # Not valid JSON; the comma-split fallback coerces each item.
        result = parse_attrs(_attrs("items={['a', 'b']}"))
        assert result == {"items": ["a", "b"]}

    def test_empty_list(self):
        result = parse_attrs(_attrs("items={[]}"))
        assert result == {"items": []}

    def test_list_does_not_shadow_data_ref(self):
        # A bare identifier in braces is still a DataRef, not a list.
        result = parse_attrs(_attrs("data={my_query}"))
        assert result == {"data": DataRef("my_query")}

    def test_zero_value(self):
        """Zero is parsed correctly."""
        result = parse_attrs(_attrs("count=0"))
        assert result == {"count": 0}

    def test_negative_zero_float(self):
        """Negative zero float is parsed."""
        result = parse_attrs(_attrs("value=-0.0"))
        assert result == {"value": -0.0}

    def test_scientific_notation(self):
        """Scientific notation numbers are preserved as strings."""
        # The regex doesn't handle scientific notation, so it stays as string
        result = parse_attrs(_attrs("value=1e10"))
        assert result == {"value": "1e10"}

    def test_unicode_in_string(self):
        """Unicode characters in string values are preserved."""
        result = parse_attrs(_attrs('label="Hello 世界"'))
        assert result == {"label": "Hello 世界"}
