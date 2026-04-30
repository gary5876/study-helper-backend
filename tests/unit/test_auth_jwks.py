"""JWT auth tests covering the asymmetric (JWKS) verification path of app/core/auth.py.

기존 test_auth.py가 HS256 경로만 다루고 있어 RS256/JWKS 경로는 전혀 미커버였음.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import HTTPException

from app.core.auth import _JWKS_CACHE, get_current_user


@pytest.fixture(autouse=True)
def _reset_jwks_cache():
    _JWKS_CACHE["keys"] = None
    _JWKS_CACHE["url"] = None
    _JWKS_CACHE["fetched_at"] = 0.0
    yield
    _JWKS_CACHE["keys"] = None


def _patch_jwt(monkeypatch, *, alg="RS256", kid="key-1", iss="https://x.supabase.co",
               sub="u1", email="u@e.com"):
    monkeypatch.setattr(
        "app.core.auth.jwt.get_unverified_header",
        lambda _t: {"alg": alg, "kid": kid},
    )
    monkeypatch.setattr(
        "app.core.auth.jwt.get_unverified_claims",
        lambda _t: {"iss": iss, "sub": sub, "email": email},
    )
    monkeypatch.setattr(
        "app.core.auth.jwt.decode",
        lambda *a, **kw: {"sub": sub, "email": email},
    )


def _patch_jwks_response(monkeypatch, keys):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={"keys": keys})
    monkeypatch.setattr("app.core.auth.httpx.get", MagicMock(return_value=resp))


async def test_asymmetric_token_returns_user(monkeypatch):
    _patch_jwt(monkeypatch)
    _patch_jwks_response(monkeypatch, [{"kid": "key-1", "kty": "RSA"}])
    result = await get_current_user(authorization="Bearer faketoken")
    assert result == {"user_id": "u1", "email": "u@e.com"}


async def test_jwks_kid_miss_triggers_refetch(monkeypatch):
    """첫 조회에서 kid 매칭 실패 → 캐시 무효화 후 재조회 → 두 번째 응답에서 매칭 성공."""
    _patch_jwt(monkeypatch, kid="key-2")
    call_count = {"n": 0}

    def get_resp(*args, **kwargs):
        call_count["n"] += 1
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        if call_count["n"] == 1:
            resp.json = MagicMock(return_value={"keys": [{"kid": "key-1", "kty": "RSA"}]})
        else:
            resp.json = MagicMock(return_value={"keys": [{"kid": "key-2", "kty": "RSA"}]})
        return resp

    monkeypatch.setattr("app.core.auth.httpx.get", get_resp)
    result = await get_current_user(authorization="Bearer faketoken")
    assert result == {"user_id": "u1", "email": "u@e.com"}
    assert call_count["n"] == 2


async def test_jwks_no_matching_kid_after_refetch_raises_401(monkeypatch):
    _patch_jwt(monkeypatch, kid="missing-kid")
    _patch_jwks_response(monkeypatch, [{"kid": "other-kid", "kty": "RSA"}])
    with pytest.raises(HTTPException) as exc:
        await get_current_user(authorization="Bearer faketoken")
    assert exc.value.status_code == 401


async def test_jwks_fetch_failure_raises_503(monkeypatch):
    _patch_jwt(monkeypatch)
    monkeypatch.setattr(
        "app.core.auth.httpx.get",
        MagicMock(side_effect=httpx.HTTPError("network down")),
    )
    with pytest.raises(HTTPException) as exc:
        await get_current_user(authorization="Bearer faketoken")
    assert exc.value.status_code == 503


async def test_unsupported_alg_raises_401(monkeypatch):
    _patch_jwt(monkeypatch, alg="NONE")
    with pytest.raises(HTTPException) as exc:
        await get_current_user(authorization="Bearer faketoken")
    assert exc.value.status_code == 401


async def test_asymmetric_missing_iss_raises_401(monkeypatch):
    monkeypatch.setattr(
        "app.core.auth.jwt.get_unverified_header",
        lambda _t: {"alg": "RS256", "kid": "key-1"},
    )
    monkeypatch.setattr(
        "app.core.auth.jwt.get_unverified_claims",
        lambda _t: {"sub": "u1"},  # iss 누락
    )
    with pytest.raises(HTTPException) as exc:
        await get_current_user(authorization="Bearer faketoken")
    assert exc.value.status_code == 401
