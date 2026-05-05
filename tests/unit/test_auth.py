"""Unit tests for JWT auth dependency (app/core/auth.py)."""
from __future__ import annotations

import time
import pytest
from unittest.mock import patch
from fastapi import HTTPException
from jose import jwt

from app.core.auth import get_current_user, require_current_user


def _make_token(secret: str, expired: bool = False, audience: str = "authenticated") -> str:
    now = int(time.time())
    exp = now - 60 if expired else now + 3600
    payload = {"sub": "user-uuid-123", "email": "test@example.com", "exp": exp, "aud": audience}
    return jwt.encode(payload, secret, algorithm="HS256")


# ─────────────────────────────────────────
# get_current_user
# ─────────────────────────────────────────

async def test_no_header_returns_none():
    result = await get_current_user(authorization=None)
    assert result is None


async def test_malformed_header_raises_401():
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(authorization="Token abc123")
    assert exc_info.value.status_code == 401


async def test_valid_token_returns_user():
    secret = "test-secret-key"
    with patch("app.core.auth.settings") as mock_settings:
        mock_settings.SUPABASE_JWT_SECRET = secret
        token = _make_token(secret)
        result = await get_current_user(authorization=f"Bearer {token}")
    assert result is not None
    assert result["user_id"] == "user-uuid-123"
    assert result["email"] == "test@example.com"


async def test_expired_token_raises_401():
    secret = "test-secret-key"
    with patch("app.core.auth.settings") as mock_settings:
        mock_settings.SUPABASE_JWT_SECRET = secret
        token = _make_token(secret, expired=True)
        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(authorization=f"Bearer {token}")
    assert exc_info.value.status_code == 401
    assert "만료" in exc_info.value.detail


async def test_invalid_signature_raises_401():
    secret = "test-secret-key"
    wrong_secret = "wrong-secret"
    with patch("app.core.auth.settings") as mock_settings:
        mock_settings.SUPABASE_JWT_SECRET = secret
        token = _make_token(wrong_secret)
        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(authorization=f"Bearer {token}")
    assert exc_info.value.status_code == 401


async def test_missing_jwt_secret_raises_500():
    with patch("app.core.auth.settings") as mock_settings:
        mock_settings.SUPABASE_JWT_SECRET = None
        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(authorization="Bearer sometoken")
    assert exc_info.value.status_code == 500


# ─────────────────────────────────────────
# require_current_user
# ─────────────────────────────────────────

async def test_require_no_header_raises_401():
    with pytest.raises(HTTPException) as exc_info:
        await require_current_user(authorization=None)
    assert exc_info.value.status_code == 401


async def test_require_valid_token_returns_user():
    secret = "test-secret-key"
    with patch("app.core.auth.settings") as mock_settings:
        mock_settings.SUPABASE_JWT_SECRET = secret
        token = _make_token(secret)
        result = await require_current_user(authorization=f"Bearer {token}")
    assert result["user_id"] == "user-uuid-123"
