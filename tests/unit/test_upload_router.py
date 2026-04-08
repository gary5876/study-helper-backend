"""Unit tests for the /upload endpoint (app/routers/upload.py)."""
from __future__ import annotations

import io
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from app.main import app
from app.services.session_store import init_store
from app.services.pdf_parser import ParsedDocument, ParsedSection


@pytest.fixture(autouse=True)
async def setup_store():
    await init_store(redis_url=None)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _fake_doc(**kwargs) -> ParsedDocument:
    defaults = dict(
        sections=[ParsedSection(title="Intro", content="Some content.", page_range=(1, 1))],
        full_text="Some content.",
        page_count=3,
        word_count=100,
        truncated=False,
        warning=None,
    )
    defaults.update(kwargs)
    return ParsedDocument(**defaults)


# ─────────────────────────────────────────
# API key validation
# ─────────────────────────────────────────

def test_upload_missing_api_key_returns_400(client):
    res = client.post(
        "/upload",
        data={"api_key": "", "plan": "paid"},
        files={"file": ("test.pdf", io.BytesIO(b"data"), "application/pdf")},
    )
    assert res.status_code == 400
    assert "API key" in res.json()["detail"]


def test_upload_short_api_key_returns_400(client):
    res = client.post(
        "/upload",
        data={"api_key": "short", "plan": "paid"},
        files={"file": ("test.pdf", io.BytesIO(b"data"), "application/pdf")},
    )
    assert res.status_code == 400


# ─────────────────────────────────────────
# File type validation
# ─────────────────────────────────────────

def test_upload_non_pdf_returns_400(client):
    res = client.post(
        "/upload",
        data={"api_key": "sk-ant-valid-key-12345", "plan": "paid"},
        files={"file": ("notes.txt", io.BytesIO(b"hello"), "text/plain")},
    )
    assert res.status_code == 400
    assert "PDF" in res.json()["detail"]


def test_upload_empty_file_returns_400(client):
    with patch("app.routers.upload.parse_pdf") as mock_parse:
        res = client.post(
            "/upload",
            data={"api_key": "sk-ant-valid-key-12345", "plan": "paid"},
            files={"file": ("empty.pdf", io.BytesIO(b""), "application/pdf")},
        )
    assert res.status_code == 400


# ─────────────────────────────────────────
# Successful upload
# ─────────────────────────────────────────

@patch("app.routers.upload.parse_pdf")
def test_upload_success_returns_session(mock_parse, client):
    mock_parse.return_value = _fake_doc()
    res = client.post(
        "/upload",
        data={"api_key": "sk-ant-valid-key-12345", "plan": "paid"},
        files={"file": ("doc.pdf", io.BytesIO(b"pdfbytes"), "application/pdf")},
    )
    assert res.status_code == 200
    data = res.json()
    assert "session_id" in data
    assert data["page_count"] == 3
    assert data["word_count"] == 100
    assert data["status"] == "uploaded"
    assert data["pdf_name"] == "doc.pdf"


# ─────────────────────────────────────────
# PDFParseError propagation
# ─────────────────────────────────────────

@patch("app.routers.upload.parse_pdf")
def test_upload_scanned_pdf_returns_422(mock_parse, client):
    from app.core.exceptions import PDFParseError
    mock_parse.side_effect = PDFParseError("Scanned PDF", status_code=422)
    res = client.post(
        "/upload",
        data={"api_key": "sk-ant-valid-key-12345", "plan": "paid"},
        files={"file": ("scan.pdf", io.BytesIO(b"pdfbytes"), "application/pdf")},
    )
    assert res.status_code == 422


@patch("app.routers.upload.parse_pdf")
def test_upload_password_protected_returns_400(mock_parse, client):
    from app.core.exceptions import PDFParseError
    mock_parse.side_effect = PDFParseError("Password protected", status_code=400)
    res = client.post(
        "/upload",
        data={"api_key": "sk-ant-valid-key-12345", "plan": "paid"},
        files={"file": ("locked.pdf", io.BytesIO(b"pdfbytes"), "application/pdf")},
    )
    assert res.status_code == 400


# ─────────────────────────────────────────
# Warning propagation (truncated PDF)
# ─────────────────────────────────────────

@patch("app.routers.upload.parse_pdf")
def test_upload_truncated_pdf_still_succeeds(mock_parse, client):
    mock_parse.return_value = _fake_doc(
        truncated=True,
        warning="PDF has 60 pages; only the first 50 pages were processed.",
    )
    res = client.post(
        "/upload",
        data={"api_key": "sk-ant-valid-key-12345", "plan": "paid"},
        files={"file": ("big.pdf", io.BytesIO(b"pdfbytes"), "application/pdf")},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "uploaded"
