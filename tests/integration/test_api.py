"""Integration tests for the FastAPI endpoints using TestClient."""
import io
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from app.main import app
from app.services.session_store import init_store


@pytest.fixture(autouse=True)
async def setup_store():
    """Use in-memory store (no Redis) for tests."""
    await init_store(redis_url=None)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _minimal_pdf_bytes() -> bytes:
    """Return minimal valid PDF content (text-based) for testing."""
    # This is a minimal but valid-enough PDF that pdfplumber can attempt to open
    return b"""%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj
4 0 obj<</Length 44>>stream
BT /F1 12 Tf 100 700 Td (Hello World) Tj ET
endstream
endobj
5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj
xref
0 6
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
0000000266 00000 n
0000000360 00000 n
trailer<</Size 6/Root 1 0 R>>
startxref
441
%%EOF"""


# ─────────────────────────────────────────
# Health check
# ─────────────────────────────────────────

def test_health_check(client):
    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"


# ─────────────────────────────────────────
# Upload endpoint
# ─────────────────────────────────────────

def test_upload_missing_api_key(client):
    res = client.post(
        "/upload",
        data={"api_key": ""},
        files={"file": ("test.pdf", io.BytesIO(b"fake"), "application/pdf")},
    )
    assert res.status_code == 400


def test_upload_wrong_file_type(client):
    res = client.post(
        "/upload",
        data={"api_key": "sk-ant-test-key-12345"},
        files={"file": ("test.txt", io.BytesIO(b"hello"), "text/plain")},
    )
    assert res.status_code == 400


@patch("app.routers.upload.parse_pdf")
def test_upload_success(mock_parse, client):
    from app.services.pdf_parser import ParsedDocument, ParsedSection
    mock_parse.return_value = ParsedDocument(
        sections=[ParsedSection(title="Intro", content="Content here.", page_range=(1, 1))],
        full_text="Content here.",
        page_count=5,
        word_count=120,
        truncated=False,
    )

    res = client.post(
        "/upload",
        data={"api_key": "sk-ant-test-key-valid-12345"},
        files={"file": ("test.pdf", io.BytesIO(b"fake_pdf"), "application/pdf")},
    )
    assert res.status_code == 200
    data = res.json()
    assert "session_id" in data
    assert data["page_count"] == 5
    assert data["status"] == "uploaded"


# ─────────────────────────────────────────
# Status endpoint
# ─────────────────────────────────────────

def test_status_not_found(client):
    res = client.get("/status/nonexistent-session-id")
    assert res.status_code == 404


@patch("app.routers.upload.parse_pdf")
def test_status_after_upload(mock_parse, client):
    from app.services.pdf_parser import ParsedDocument, ParsedSection
    mock_parse.return_value = ParsedDocument(
        sections=[ParsedSection(title="S1", content="Some content.", page_range=(1, 1))],
        full_text="Some content.",
        page_count=2,
        word_count=50,
        truncated=False,
    )
    upload_res = client.post(
        "/upload",
        data={"api_key": "sk-ant-test-key-valid-12345"},
        files={"file": ("test.pdf", io.BytesIO(b"fake"), "application/pdf")},
    )
    session_id = upload_res.json()["session_id"]

    status_res = client.get(f"/status/{session_id}")
    assert status_res.status_code == 200
    data = status_res.json()
    assert data["status"] == "uploaded"


# ─────────────────────────────────────────
# Delete endpoint
# ─────────────────────────────────────────

@patch("app.routers.upload.parse_pdf")
def test_delete_session(mock_parse, client):
    from app.services.pdf_parser import ParsedDocument, ParsedSection
    mock_parse.return_value = ParsedDocument(
        sections=[ParsedSection(title="S1", content="Content.", page_range=(1, 1))],
        full_text="Content.",
        page_count=1,
        word_count=20,
        truncated=False,
    )
    upload_res = client.post(
        "/upload",
        data={"api_key": "sk-ant-test-key-valid-12345"},
        files={"file": ("test.pdf", io.BytesIO(b"fake"), "application/pdf")},
    )
    session_id = upload_res.json()["session_id"]

    del_res = client.delete(f"/session/{session_id}")
    assert del_res.status_code == 200
    assert del_res.json()["deleted"] is True

    # Should be gone now
    status_res = client.get(f"/status/{session_id}")
    assert status_res.status_code == 404
