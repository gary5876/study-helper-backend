"""Unit tests for custom exceptions and their handlers."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.exceptions import (
    GenerationError,
    PDFParseError,
    SessionNotFoundError,
    ValidationError,
    generation_error_handler,
    pdf_parse_error_handler,
    session_not_found_handler,
    unhandled_exception_handler,
)


# ─────────────────────────────────────────
# Exception classes
# ─────────────────────────────────────────

def test_pdf_parse_error_default_status():
    exc = PDFParseError("bad pdf")
    assert exc.status_code == 400
    assert exc.message == "bad pdf"


def test_pdf_parse_error_custom_status():
    exc = PDFParseError("scanned", status_code=422)
    assert exc.status_code == 422


def test_generation_error_default_status():
    exc = GenerationError("gen failed")
    assert exc.status_code == 500


def test_session_not_found_message():
    exc = SessionNotFoundError("abc-123")
    assert "abc-123" in exc.message


def test_validation_error():
    exc = ValidationError("bad input")
    assert exc.message == "bad input"


# ─────────────────────────────────────────
# Handlers via mini FastAPI app
# ─────────────────────────────────────────

def _make_test_app() -> tuple[FastAPI, TestClient]:
    app = FastAPI()
    app.add_exception_handler(PDFParseError, pdf_parse_error_handler)
    app.add_exception_handler(GenerationError, generation_error_handler)
    app.add_exception_handler(SessionNotFoundError, session_not_found_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    @app.get("/pdf-error")
    async def _pdf_error():
        raise PDFParseError("scanned document", status_code=422)

    @app.get("/gen-error")
    async def _gen_error():
        raise GenerationError("api failed", status_code=502)

    @app.get("/session-error")
    async def _session_error():
        raise SessionNotFoundError("sess-999")

    @app.get("/unhandled")
    async def _unhandled():
        raise RuntimeError("unexpected crash")

    return app, TestClient(app, raise_server_exceptions=False)


_app, _client = _make_test_app()


def test_pdf_parse_error_handler_response():
    res = _client.get("/pdf-error")
    assert res.status_code == 422
    body = res.json()
    assert body["error"] == "pdf_parse_error"
    assert "scanned" in body["message"]


def test_generation_error_handler_response():
    res = _client.get("/gen-error")
    assert res.status_code == 502
    body = res.json()
    assert body["error"] == "generation_error"


def test_session_not_found_handler_response():
    res = _client.get("/session-error")
    assert res.status_code == 404
    body = res.json()
    assert body["error"] == "session_not_found"
    assert "sess-999" in body["message"]


def test_unhandled_exception_handler_response():
    res = _client.get("/unhandled")
    assert res.status_code == 500
    body = res.json()
    assert body["error"] == "internal_server_error"
    assert "unexpected" not in body["message"]  # stack trace not leaked
