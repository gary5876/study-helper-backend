"""Unit tests for app/core/validators.py — 모든 plan 분기 + 엣지 케이스."""
from __future__ import annotations

import pytest

from app.core.validators import is_valid_uuid, validate_api_key


# ─────────────────────────────────────────
# validate_api_key — plan별 정책
# ─────────────────────────────────────────

def test_validate_api_key_paid_requires_sk_ant_prefix():
    assert validate_api_key("sk-ant-abc12345678901234567", "paid") is True
    assert validate_api_key("sk-other-abc12345678901234567", "paid") is False


def test_validate_api_key_paid_rejects_short_key():
    assert validate_api_key("sk-ant-short", "paid") is False


def test_validate_api_key_gpt_requires_sk_prefix():
    assert validate_api_key("sk-abc12345678901234567", "gpt") is True
    assert validate_api_key("xx-abc12345678901234567", "gpt") is False


def test_validate_api_key_gpt_rejects_short():
    assert validate_api_key("sk-short", "gpt") is False


def test_validate_api_key_timely_requires_min_length():
    assert validate_api_key("a" * 20, "timely") is True
    assert validate_api_key("a" * 19, "timely") is False


def test_validate_api_key_unknown_plan_returns_false():
    assert validate_api_key("a" * 30, "free") is False
    assert validate_api_key("a" * 30, "") is False


def test_validate_api_key_empty_string_returns_false():
    assert validate_api_key("", "paid") is False
    assert validate_api_key("", "gpt") is False
    assert validate_api_key("", "timely") is False


# ─────────────────────────────────────────
# is_valid_uuid
# ─────────────────────────────────────────

def test_is_valid_uuid_accepts_proper_uuid():
    assert is_valid_uuid("00000000-0000-0000-0000-000000000000") is True
    assert is_valid_uuid("d33b1d10-cf72-4f6f-8c18-fae3ad9bff2d") is True


def test_is_valid_uuid_accepts_no_hyphens():
    """UUID 모듈은 하이픈 없는 32자 hex도 받아들임."""
    assert is_valid_uuid("d33b1d10cf724f6f8c18fae3ad9bff2d") is True


def test_is_valid_uuid_rejects_garbage():
    assert is_valid_uuid("not-a-uuid") is False
    assert is_valid_uuid("") is False
    assert is_valid_uuid("12345") is False


def test_is_valid_uuid_handles_none():
    """잘못된 타입(None) 들어와도 예외 없이 False."""
    assert is_valid_uuid(None) is False  # type: ignore[arg-type]
