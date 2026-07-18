# -*- coding: utf-8 -*-
"""Fixture กลางของชุดเทสต์."""

import pytest

from utils.cache import clear_all_caches


@pytest.fixture(autouse=True)
def _isolate_ttl_caches():
    """ล้าง TTL cache (utils/cache.py) ก่อน-หลังทุกเทสต์ — กันผลลัพธ์รั่วข้ามเคส."""
    clear_all_caches()
    yield
    clear_all_caches()
