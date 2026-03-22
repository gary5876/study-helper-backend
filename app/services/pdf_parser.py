"""PDF text extraction and structure detection using pdfplumber."""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

import pdfplumber

from app.core.config import get_settings
from app.core.exceptions import PDFParseError

settings = get_settings()


@dataclass
class ParsedSection:
    title: str
    content: str
    page_range: tuple[int, int]


@dataclass
class ParsedDocument:
    sections: list[ParsedSection] = field(default_factory=list)
    full_text: str = ""
    page_count: int = 0
    word_count: int = 0
    truncated: bool = False
    warning: str | None = None


def _clean_text(text: str) -> str:
    """Remove common PDF artifacts: multiple blank lines, stray page numbers."""
    # Collapse runs of whitespace lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Remove lone page numbers (a line with only digits)
    text = re.sub(r"^\s*\d+\s*$", "", text, flags=re.MULTILINE)
    return text.strip()


def _is_heading(word_dict: dict) -> bool:
    """Heuristic: bold text or font size >= 14pt is likely a heading."""
    size = word_dict.get("size", 0) or 0
    fontname = word_dict.get("fontname", "") or ""
    is_bold = "Bold" in fontname or "bold" in fontname or "Heavy" in fontname
    return is_bold or size >= 14


def _extract_sections(pages: list) -> list[ParsedSection]:
    """Group page text into logical sections by detected headings."""
    sections: list[ParsedSection] = []
    current_title = "Introduction"
    current_lines: list[str] = []
    current_start = 1

    for page_num, page in enumerate(pages, start=1):
        words = page.extract_words(extra_attrs=["fontname", "size"]) or []
        page_text = page.extract_text() or ""

        # Build a set of line-start positions that are headings
        heading_lines: set[str] = set()
        for w in words:
            if _is_heading(w):
                heading_lines.add(w.get("text", ""))

        for line in page_text.split("\n"):
            stripped = line.strip()
            if not stripped:
                continue
            # Check if ANY word in this line was detected as a heading
            if any(h in stripped for h in heading_lines) and len(stripped) < 120:
                # Flush current section
                if current_lines:
                    sections.append(
                        ParsedSection(
                            title=current_title,
                            content=_clean_text("\n".join(current_lines)),
                            page_range=(current_start, page_num),
                        )
                    )
                current_title = stripped
                current_lines = []
                current_start = page_num
            else:
                current_lines.append(stripped)

    # Flush last section
    if current_lines:
        sections.append(
            ParsedSection(
                title=current_title,
                content=_clean_text("\n".join(current_lines)),
                page_range=(current_start, len(pages)),
            )
        )

    # If no sections detected, treat entire doc as one section
    if not sections:
        full = "\n".join(
            _clean_text(p.extract_text() or "") for p in pages
        )
        sections.append(
            ParsedSection(
                title="Document Content",
                content=full,
                page_range=(1, len(pages)),
            )
        )

    return sections


def _chunk_sections(sections: list[ParsedSection], max_tokens: int) -> list[ParsedSection]:
    """Split large sections so each fits within max_tokens (rough word approximation)."""
    chunked: list[ParsedSection] = []
    for sec in sections:
        words = sec.content.split()
        if len(words) <= max_tokens:
            chunked.append(sec)
            continue
        # Split into sub-chunks
        for i, start in enumerate(range(0, len(words), max_tokens)):
            chunk_words = words[start : start + max_tokens]
            chunked.append(
                ParsedSection(
                    title=f"{sec.title} (part {i + 1})",
                    content=" ".join(chunk_words),
                    page_range=sec.page_range,
                )
            )
    return chunked


def parse_pdf(file_bytes: bytes, filename: str = "document.pdf") -> ParsedDocument:
    """
    Parse PDF bytes into a structured document.

    Raises PDFParseError for scanned / password-protected / corrupt PDFs.
    Truncates to MAX_PDF_PAGES with a warning if the PDF is too long.
    """
    if len(file_bytes) > settings.MAX_PDF_SIZE_MB * 1024 * 1024:
        raise PDFParseError(
            f"PDF exceeds maximum size of {settings.MAX_PDF_SIZE_MB}MB.", status_code=413
        )

    try:
        pdf = pdfplumber.open(io.BytesIO(file_bytes))
    except Exception as exc:
        raise PDFParseError(f"Cannot open PDF: {exc}", status_code=400) from exc

    # Password-protected check (pdfminer renamed is_encrypted → encryption)
    _encrypted = getattr(pdf.doc, 'is_encrypted', None) or bool(getattr(pdf.doc, 'encryption', None))
    if _encrypted:
        raise PDFParseError(
            "PDF is password-protected. Please provide an unlocked file.", status_code=400
        )

    total_pages = len(pdf.pages)
    truncated = total_pages > settings.MAX_PDF_PAGES
    pages = pdf.pages[: settings.MAX_PDF_PAGES]

    # Scanned-PDF detection: if text extraction yields < 50 chars total
    sample_text = "".join((p.extract_text() or "") for p in pages[:5])
    if len(sample_text.strip()) < 50:
        raise PDFParseError(
            "This PDF appears to be a scanned image without a text layer. "
            "OCR is not supported. Please use a text-based PDF.",
            status_code=422,
        )

    sections = _extract_sections(pages)
    sections = _chunk_sections(sections, settings.CHUNK_TOKEN_SIZE)

    full_text = "\n\n".join(
        f"## {s.title}\n{s.content}" for s in sections
    )
    word_count = len(full_text.split())

    doc = ParsedDocument(
        sections=sections,
        full_text=full_text,
        page_count=total_pages,
        word_count=word_count,
        truncated=truncated,
    )
    if truncated:
        doc.warning = (
            f"PDF has {total_pages} pages; only the first "
            f"{settings.MAX_PDF_PAGES} pages were processed."
        )
    return doc
