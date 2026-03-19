"""Unit tests for the PDF parser service."""
import io
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from app.core.exceptions import PDFParseError
from app.services.pdf_parser import parse_pdf, _clean_text, _is_heading


# ─────────────────────────────────────────
# Helper: minimal mock pdfplumber page
# ─────────────────────────────────────────

def _make_page(text: str, words: list[dict] | None = None):
    page = MagicMock()
    page.extract_text.return_value = text
    page.extract_words.return_value = words or []
    return page


def _make_pdf(pages, is_encrypted=False):
    pdf = MagicMock()
    pdf.pages = pages
    pdf.doc = MagicMock()
    pdf.doc.is_encrypted = is_encrypted
    return pdf


# ─────────────────────────────────────────
# _clean_text
# ─────────────────────────────────────────

def test_clean_text_removes_page_numbers():
    text = "Hello world\n\n42\n\nMore content"
    cleaned = _clean_text(text)
    assert "42" not in cleaned.split("\n")
    assert "Hello world" in cleaned


def test_clean_text_collapses_blank_lines():
    text = "Line 1\n\n\n\n\nLine 2"
    cleaned = _clean_text(text)
    assert "\n\n\n" not in cleaned


# ─────────────────────────────────────────
# _is_heading
# ─────────────────────────────────────────

def test_is_heading_detects_bold():
    assert _is_heading({"fontname": "Arial-Bold", "size": 12}) is True


def test_is_heading_detects_large_font():
    assert _is_heading({"fontname": "Arial", "size": 16}) is True


def test_is_heading_normal_text():
    assert _is_heading({"fontname": "Arial", "size": 10}) is False


# ─────────────────────────────────────────
# parse_pdf
# ─────────────────────────────────────────

@patch("app.services.pdf_parser.pdfplumber.open")
def test_parse_pdf_happy_path(mock_open):
    pages = [_make_page("Introduction\nThis is sample text about topic A.\n" * 20)]
    mock_open.return_value.__enter__ = lambda s: s
    mock_open.return_value.__exit__ = MagicMock(return_value=False)
    mock_pdf = _make_pdf(pages)
    mock_open.return_value = mock_pdf

    result = parse_pdf(b"fake_pdf_bytes", "test.pdf")
    assert result.page_count == 1
    assert result.word_count > 0
    assert len(result.sections) > 0
    assert result.truncated is False


@patch("app.services.pdf_parser.pdfplumber.open")
def test_parse_pdf_scanned_raises(mock_open):
    pages = [_make_page("")]  # empty text → scanned PDF
    mock_pdf = _make_pdf(pages)
    mock_open.return_value = mock_pdf

    with pytest.raises(PDFParseError) as exc_info:
        parse_pdf(b"fake_bytes", "scanned.pdf")
    assert "scanned" in exc_info.value.message.lower() or "text layer" in exc_info.value.message.lower()
    assert exc_info.value.status_code == 422


@patch("app.services.pdf_parser.pdfplumber.open")
def test_parse_pdf_password_protected_raises(mock_open):
    mock_pdf = _make_pdf([], is_encrypted=True)
    mock_open.return_value = mock_pdf

    with pytest.raises(PDFParseError) as exc_info:
        parse_pdf(b"fake_bytes", "locked.pdf")
    assert exc_info.value.status_code == 400
    assert "password" in exc_info.value.message.lower()


def test_parse_pdf_file_too_large_raises():
    big_bytes = b"x" * (21 * 1024 * 1024)  # 21MB
    with pytest.raises(PDFParseError) as exc_info:
        parse_pdf(big_bytes, "big.pdf")
    assert exc_info.value.status_code == 413


@patch("app.services.pdf_parser.pdfplumber.open")
def test_parse_pdf_truncation(mock_open):
    # Create 60 pages (> MAX_PDF_PAGES=50)
    pages = [_make_page(f"Section {i}\nContent for section {i}.\n" * 5) for i in range(60)]
    mock_pdf = _make_pdf(pages)
    mock_open.return_value = mock_pdf

    result = parse_pdf(b"fake_bytes", "big.pdf")
    assert result.truncated is True
    assert result.warning is not None
    assert result.page_count == 60  # total, even though only 50 processed
