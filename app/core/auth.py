"""Supabase JWT verification dependency for FastAPI endpoints."""
from __future__ import annotations

from fastapi import Header, HTTPException
from jose import ExpiredSignatureError, JWTError, jwt

from app.core.config import get_settings

settings = get_settings()


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

    if not settings.SUPABASE_JWT_SECRET:
        raise HTTPException(status_code=500, detail="서버 설정 오류: JWT Secret 미설정")

    try:
        payload = jwt.decode(
            token,
            settings.SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated",
        )
        return {"user_id": payload["sub"], "email": payload.get("email")}
    except ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="토큰이 만료되었습니다")
    except JWTError:
        raise HTTPException(status_code=401, detail="유효하지 않은 토큰")


async def require_current_user(authorization: str = Header(default=None)) -> dict:
    """
    Like get_current_user but rejects guests (returns 401 when no header).
    Use this for /user/* endpoints that require authentication.
    """
    user = await get_current_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다")
    return user
