from __future__ import annotations

from itertools import pairwise

from app.rag.chunker import chunk_document
from app.rag.extract import ExtractedDocument, Heading, Page


def words(text: str) -> int:
    return len(text.split())


def _page(page_no: int, text: str, headings: tuple[Heading, ...] = ()) -> Page:
    return Page(page_no=page_no, text=text, headings=headings, is_image_only=False)


def _heading(text: str, page_no: int, offset: int) -> Heading:
    return Heading(level=1, text=text, page_no=page_no, char_offset=offset)


def test_chunker_respects_sentence_windows_and_overlap() -> None:
    body = "Reactants become products. " * 40
    text = f"Chemical Reactions\n\n{body}"
    document = ExtractedDocument(
        page_count=1,
        pages=(_page(1, text, (_heading("Chemical Reactions", 1, 0),)),),
    )

    chunks = chunk_document(document, words, max_tokens=25, overlap=8)

    assert len(chunks) > 2
    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))
    assert all(chunk.token_count <= 25 for chunk in chunks)
    assert all(chunk.page_start == chunk.page_end == 1 for chunk in chunks)
    assert all(chunk.section_title == "Chemical Reactions" for chunk in chunks)
    assert all(chunk.text.rstrip().endswith(".") for chunk in chunks)
    assert all("Chemical Reactions" not in chunk.text for chunk in chunks)

    for previous, following in pairwise(chunks):
        assert following.page_start >= previous.page_start
        previous_sentences = [part.strip() for part in previous.text.split(". ") if part.strip()]
        assert any(sentence in following.text for sentence in previous_sentences[-2:])


def test_chunker_splits_sections_at_headings() -> None:
    first = "Reactants become products. " * 12
    second = "Catalysts change the reaction rate. " * 12
    text = f"First Section\n\n{first}\n\nSecond Section\n\n{second}"
    second_offset = text.index("Second Section")
    document = ExtractedDocument(
        page_count=1,
        pages=(
            _page(
                1,
                text,
                (
                    _heading("First Section", 1, 0),
                    _heading("Second Section", 1, second_offset),
                ),
            ),
        ),
    )

    chunks = chunk_document(document, words, max_tokens=20, overlap=5)
    titles = {chunk.section_title for chunk in chunks}
    assert titles == {"First Section", "Second Section"}


def test_chunker_keeps_page_spans_monotonic_across_pages() -> None:
    page_one = "Section A\n\n" + ("Reactants become products. " * 20)
    page_two = "Catalysts change the rate. " * 20
    document = ExtractedDocument(
        page_count=2,
        pages=(
            _page(1, page_one, (_heading("Section A", 1, 0),)),
            _page(2, page_two),
        ),
    )

    chunks = chunk_document(document, words, max_tokens=30, overlap=6)

    assert chunks[-1].page_end == 2
    for previous, following in pairwise(chunks):
        assert following.page_start >= previous.page_start
        assert following.page_end >= previous.page_end


def test_chunker_keeps_a_single_short_section() -> None:
    document = ExtractedDocument(
        page_count=1,
        pages=(_page(1, "Short\n\nA tiny section.", (_heading("Short", 1, 0),)),),
    )

    chunks = chunk_document(document, words, max_tokens=100, overlap=10)

    assert len(chunks) == 1
    assert chunks[0].text == "A tiny section."


def test_chunker_uses_document_title_without_headings() -> None:
    document = ExtractedDocument(
        page_count=1,
        pages=(_page(1, "Reactants become products. " * 12),),
    )

    chunks = chunk_document(document, words, max_tokens=40, overlap=5)

    assert chunks
    assert all(chunk.section_title == "Document" for chunk in chunks)
