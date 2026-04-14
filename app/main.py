"""FastAPI application entry point."""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from app.core.config import get_settings
from app.core.exceptions import (
    GenerationError,
    PDFParseError,
    SessionNotFoundError,
    generation_error_handler,
    pdf_parse_error_handler,
    session_not_found_handler,
    unhandled_exception_handler,
)
from app.core.logging_config import configure_logging
from app.routers import generate, upload, user
from app.services.session_store import init_store
from app.services.question_bank import init_question_bank, close_question_bank
from app.services.user_store import init_user_store, close_user_store

configure_logging()

import logging
from prometheus_fastapi_instrumentator import Instrumentator

logger = logging.getLogger(__name__)
settings = get_settings()

# Rate limiter (keyed by remote IP) — shared instance, also used by routers
limiter = Limiter(key_func=get_remote_address, default_limits=[f"{settings.RATE_LIMIT_PER_MINUTE}/minute"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up %s v%s [%s]", settings.APP_NAME, settings.APP_VERSION, settings.ENVIRONMENT)
    logger.info("CORS allowed origins: %s", settings.ALLOWED_ORIGINS)
    await init_store(
        redis_url=settings.REDIS_URL if settings.ENVIRONMENT != "test" else None,
        tls_enabled=settings.REDIS_TLS_ENABLED,
    )
    if settings.ENVIRONMENT != "test":
        if not settings.DATABASE_URL:
            raise RuntimeError("DATABASE_URL must be set for non-test environments")
        await init_question_bank(settings.DATABASE_URL)
    if settings.ENVIRONMENT != "test" and settings.SUPABASE_DB_URL:
        await init_user_store(settings.SUPABASE_DB_URL)
    yield
    await close_question_bank()
    await close_user_store()
    logger.info("Shutting down.")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="PDF → study notes + MCQ + fill-in-blank generation API",
    lifespan=lifespan,
)

# Rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Request-ID"],
)

# Request ID middleware — injects a unique X-Request-ID header for tracing
@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


# Security headers middleware
@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if settings.ENVIRONMENT == "production":
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    return response

# Custom exception handlers
app.add_exception_handler(PDFParseError, pdf_parse_error_handler)
app.add_exception_handler(GenerationError, generation_error_handler)
app.add_exception_handler(SessionNotFoundError, session_not_found_handler)
# Catch-all: must be last
app.add_exception_handler(Exception, unhandled_exception_handler)

# Routers
app.include_router(upload.router, tags=["upload"])
app.include_router(generate.router, tags=["generate"])
app.include_router(user.router)


@app.get("/health", tags=["health"])
async def health_check():
    return {"status": "ok", "version": settings.APP_VERSION, "environment": settings.ENVIRONMENT}


# Expose Prometheus metrics at /metrics (hidden from Swagger)
Instrumentator().instrument(app).expose(app, include_in_schema=False, tags=["metrics"])
