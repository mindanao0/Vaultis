"""Utility decorators for Streamlit caching."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar, cast

import streamlit as st

F = TypeVar("F", bound=Callable[..., Any])


def cache_data_1h(func: F) -> F:
    """Cache function results for 1 hour via Streamlit."""
    return cast(F, st.cache_data(ttl=3600, show_spinner=False)(func))
