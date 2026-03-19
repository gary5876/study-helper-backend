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
from app.routers import generate, upload
from app.services.session_store import init_store

configure_logging()

import logging
from prometheus_fastapi_instrumentator import Instrumentator

logger = logging.getLogger(__name__)
settings = get_settings()

# Rate limiter (keyed by remote IP)
limiter = Limiter(key_func=get_remote_address, default_limits=[f"{settings.RATE_LIMIT_PER_MINUTE}/minute"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up %s v%s [%s]", settings.APP_NAME, settings.APP_VERSION, settings.ENVIRONMENT)
    await init_store(
        redis_url=settings.REDIS_URL if settings.ENVIRONMENT != "test" else None,
        tls_enabled=settings.REDIS_TLS_ENABLED,
    )
    yield
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
    allow_methods=["*"],
    allow_headers=["*"],
)

# Request ID middleware — injects a unique X-Request-ID header for tracing
@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
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


@app.get("/health", tags=["health"])
async def health_check():
    return {"status": "ok", "version": settings.APP_VERSION, "environment": settings.ENVIRONMENT}


# Expose Prometheus metrics at /metrics (hidden from Swagger)
Instrumentator().instrument(app).expose(app, include_in_schema=False, tags=["metrics"])
