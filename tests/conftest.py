"""Shared pytest fixtures.

테스트 환경에서는 rate limiter를 비활성화해 같은 client IP(testclient)에서 누적된
요청이 다른 테스트의 결과에 영향을 주지 않도록 한다. (`/upload` 30/min 등)
"""
from __future__ import annotations

import pytest

from app.main import limiter


@pytest.fixture(autouse=True)
def _disable_rate_limiter():
    limiter.enabled = False
    yield
    limiter.enabled = True
