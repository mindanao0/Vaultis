# -*- coding: utf-8 -*-
"""Fixture กลางของชุดเทสต์."""

import pytest

from backend.services.cache_service import shared_cache
from utils.cache import clear_all_caches


@pytest.fixture(autouse=True)
def _isolate_ttl_caches():
    """ล้าง TTL cache ทุกตัวก่อน-หลังทุกเทสต์ — กันผลลัพธ์รั่วข้ามเคส.

    ต้องล้าง ``backend.services.cache_service.shared_cache`` ด้วย เพราะเป็น global
    ระดับ module (etf_service / market_analysis_service ใช้ร่วมกัน) ถ้าล้างแค่
    ``utils.cache`` ราคาที่เคสหนึ่ง stub ไว้จะค้างไปโผล่ในไฟล์เทสต์อื่น
    """
    clear_all_caches()
    shared_cache.clear()
    yield
    clear_all_caches()
    shared_cache.clear()
