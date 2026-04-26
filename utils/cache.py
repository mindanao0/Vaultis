"""Utility decorators for optional caching."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


def cache_data_1h(func: F) -> F:
    """No-op cache decorator for CLI-safe usage outside Streamlit runtime."""
    return func
