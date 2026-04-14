"""Supabase JWT verification dependency for FastAPI endpoints."""
from __future__ import annotations

import logging
import time

import httpx
from fastapi import Header, HTTPException
from jose import ExpiredSignatureError, JWTError, jwt

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_JWKS_CACHE: dict[str, object] = {"keys": None, "fetched_at": 0.0, "url": None}
_JWKS_TTL_SECONDS = 3600


def _jwks_url_from_issuer(issuer: str) -> str:
    return issuer.rstrip("/") + "/.well-known/jwks.json"


def _get_jwks(issuer: str) -> list[dict]:
    url = _jwks_url_from_issuer(issuer)
    now = time.time()
    if (
        _JWKS_CACHE["keys"] is not None
        and _JWKS_CACHE["url"] == url
        and now - float(_JWKS_CACHE["fetched_at"]) < _JWKS_TTL_SECONDS
    ):
        return _JWKS_CACHE["keys"]  # type: ignore[return-value]

    resp = httpx.get(url, timeout=5.0)
    resp.raise_for_status()
    keys = resp.json().get("keys", [])
    _JWKS_CACHE["keys"] = keys
    _JWKS_CACHE["fetched_at"] = now
    _JWKS_CACHE["url"] = url
    return keys


def _verify_asymmetric(token: str, header: dict, unverified: dict) -> dict:
    issuer = unverified.get("iss")
    if not issuer:
        raise JWTError("missing iss claim")
    kid = header.get("kid")
    alg = header.get("alg", "RS256")

    keys = _get_jwks(issuer)
    jwk = next((k for k in keys if k.get("kid") == kid), None)
    if jwk is None:
        _JWKS_CACHE["keys"] = None
        keys = _get_jwks(issuer)
        jwk = next((k for k in keys if k.get("kid") == kid), None)
    if jwk is None:
        raise JWTError(f"no matching JWK for kid={kid}")

    return jwt.decode(
        token,
        jwk,
        algorithms=[alg],
        audience="authenticated",
        issuer=issuer,
    )


async def get_current_user(authorization: str = Header(default=None)) -> dict | None:
    """
    Parse `Authorization: Bearer <supabase_jwt>` header.

    - No header  → returns None (guest allowed)
    - Invalid/expired token → raises 401
    - Valid token → returns {"user_id": "...", "email": "..."}
    """
    if not authorization:
        return None

    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authorization 헤더 형식이 올바르지 않습니다")

    token = authorization[len("Bearer "):].strip()

    try:
        header = jwt.get_unverified_header(token)
        unverified = jwt.get_unverified_claims(token)
        alg = header.get("alg", "")

        if alg.startswith(("RS", "ES", "PS")):
            payload = _verify_asymmetric(token, header, unverified)
        elif alg == "HS256":
            if not settings.SUPABASE_JWT_SECRET:
                logger.critical("SUPABASE_JWT_SECRET is not set — HS256 token cannot be verified")
                raise HTTPException(status_code=500, detail="서버 내부 오류가 발생했습니다")
            payload = jwt.decode(
                token,
                settings.SUPABASE_JWT_SECRET,
                algorithms=["HS256"],
                audience="authenticated",
            )
        else:
            raise JWTError(f"unsupported alg: {alg}")

        return {"user_id": payload["sub"], "email": payload.get("email")}
    except ExpiredSignatureError:
        logger.warning("Auth failure: expired token")
        raise HTTPException(status_code=401, detail="토큰이 만료되었습니다")
    except JWTError as e:
        logger.warning("Auth failure: invalid token (%s)", e)
        raise HTTPException(status_code=401, detail="유효하지 않은 토큰")
    except httpx.HTTPError as e:
        logger.error("JWKS fetch failed: %s", e)
        raise HTTPException(status_code=503, detail="인증 서비스 일시 오류")


async def require_current_user(authorization: str = Header(default=None)) -> dict:
    """
    Like get_current_user but rejects guests (returns 401 when no header).
    Use this for /user/* endpoints that require authentication.
    """
    user = await get_current_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다")
    return user
