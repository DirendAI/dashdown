"""Dashdown: markdown-driven analytics pages."""

__version__ = "0.1.0"

from dashdown.components.base import register_component, Component
from dashdown.data.base import register_connector, Connector, QueryResult
from dashdown.python_query import query

__all__ = [
    "register_component",
    "Component",
    "register_connector",
    "Connector",
    "QueryResult",
    "query",
]
