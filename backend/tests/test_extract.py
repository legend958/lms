from __future__ import annotations

import pytest

from app.rag.extract import (
    EncryptedPdfError,
    PdfParseError,
    extract_pdf,
    inspect_pdf,
)
from tests.pdf_utils import make_blank_pdf_bytes, make_encrypted_pdf_bytes, make_pdf_bytes


def test_extract_preserves_pages_and_headings() -> None:
    document = extract_pdf(make_pdf_bytes(pages=2))

    assert document.page_count == 2
    assert document.has_text
    assert [page.page_no for page in document.pages] == [1, 2]
    assert document.pages[0].headings
    assert document.pages[0].headings[0].text.startswith("Chemical Reactions")
    assert document.pages[0].headings[0].level == 1
    assert "atoms" in document.pages[0].text
    assert not document.image_only_pages


def test_blank_pdf_is_flagged_as_image_only() -> None:
    document = extract_pdf(make_blank_pdf_bytes())

    assert not document.has_text
    assert document.image_only_pages == (1,)


def test_inspect_pdf_counts_pages() -> None:
    info = inspect_pdf(make_pdf_bytes(pages=3))
    assert info.page_count == 3


def test_encrypted_pdf_is_rejected() -> None:
    with pytest.raises(EncryptedPdfError):
        extract_pdf(make_encrypted_pdf_bytes())


def test_corrupt_pdf_raises_parse_error() -> None:
    with pytest.raises(PdfParseError):
        extract_pdf(b"%PDF-1.4\nthis is not a real PDF\n%%EOF")
