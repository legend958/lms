"""Structure-aware chunking.

Sections are derived from detected headings. Sentences are packed into token
windows with sentence-level overlap, so a chunk never ends in the middle of a
sentence unless the source itself contains an unusually long sentence.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from app.rag.extract import ExtractedDocument, Heading

TokenCounter = Callable[[str], int]

MIN_CHUNK_TOKENS = 40
FALLBACK_SECTION_TITLE = "Document"
INTRODUCTION_TITLE = "Introduction"

_BOUNDARY = re.compile(r"(?<=[.!?])(?:[\"')\]]+)?\s+|\n{2,}")
_CLAUSE_BOUNDARY = re.compile(r"(?<=[,;:])\s+")
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class ChunkData:
    chunk_index: int
    text: str
    page_start: int
    page_end: int
    section_title: str | None
    token_count: int


@dataclass(slots=True)
class _Sentence:
    text: str
    page_start: int
    page_end: int
    tokens: int


@dataclass(slots=True)
class _Section:
    title: str | None
    pieces: list[tuple[str, int]]


def _normalize(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


def _sentence_spans(text: str) -> list[tuple[str, int, int]]:
    spans: list[tuple[str, int, int]] = []
    start = 0
    for match in _BOUNDARY.finditer(text):
        piece = text[start : match.start()]
        cleaned = _normalize(piece)
        if cleaned:
            spans.append((cleaned, start, match.start()))
        start = match.end()
    piece = text[start:]
    cleaned = _normalize(piece)
    if cleaned:
        spans.append((cleaned, start, len(text)))
    return spans


def _pages_for_span(spans: list[tuple[int, int, int]], start: int, end: int) -> tuple[int, int]:
    pages = [
        page for piece_start, piece_end, page in spans if piece_start < end and piece_end > start
    ]
    if not pages:
        # Empty spans are impossible for non-empty text, but keep a safe fallback.
        return (spans[0][2] if spans else 1, spans[-1][2] if spans else 1)
    return min(pages), max(pages)


def _split_long_sentence(text: str, token_counter: TokenCounter, max_tokens: int) -> list[str]:
    if token_counter(text) <= max_tokens:
        return [text]

    clauses = [part.strip() for part in _CLAUSE_BOUNDARY.split(text) if part.strip()]
    if len(clauses) > 1 and all(token_counter(part) <= max_tokens for part in clauses):
        return clauses

    words = text.split()
    pieces: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join([*current, word])
        if current and token_counter(candidate) > max_tokens:
            pieces.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        pieces.append(" ".join(current))
    return pieces or [text]


def _section_sentences(
    section: _Section,
    token_counter: TokenCounter,
    max_tokens: int,
) -> list[_Sentence]:
    if not section.pieces:
        return []

    text_parts: list[str] = []
    page_spans: list[tuple[int, int, int]] = []
    cursor = 0
    for index, (piece, page_no) in enumerate(section.pieces):
        if index:
            text_parts.append("\n\n")
            cursor += 2
        text_parts.append(piece)
        page_spans.append((cursor, cursor + len(piece), page_no))
        cursor += len(piece)

    text = "".join(text_parts)
    sentences: list[_Sentence] = []
    for sentence_text, start, end in _sentence_spans(text):
        page_start, page_end = _pages_for_span(page_spans, start, end)
        for part in _split_long_sentence(sentence_text, token_counter, max_tokens):
            sentences.append(
                _Sentence(
                    text=part,
                    page_start=page_start,
                    page_end=page_end,
                    tokens=token_counter(part),
                )
            )
    return sentences


def _pack_section(
    section: _Section,
    token_counter: TokenCounter,
    max_tokens: int,
    overlap: int,
) -> list[ChunkData]:
    sentences = _section_sentences(section, token_counter, max_tokens)
    if not sentences:
        return []

    raw_chunks: list[ChunkData] = []
    start = 0
    while start < len(sentences):
        end = start
        tokens = 0
        while end < len(sentences):
            candidate = tokens + sentences[end].tokens
            if end > start and candidate > max_tokens:
                break
            tokens = candidate
            end += 1

        chunk_sentences = sentences[start:end]
        text = " ".join(sentence.text for sentence in chunk_sentences).strip()
        if text:
            raw_chunks.append(
                ChunkData(
                    chunk_index=0,
                    text=text,
                    page_start=min(sentence.page_start for sentence in chunk_sentences),
                    page_end=max(sentence.page_end for sentence in chunk_sentences),
                    section_title=section.title,
                    token_count=token_counter(text),
                )
            )

        if end >= len(sentences):
            break

        # Walk backwards until the overlap budget is exhausted. Always advance
        # by at least one sentence so the loop cannot stall.
        next_start = end
        overlap_tokens = 0
        cursor = end - 1
        while cursor > start and overlap_tokens + sentences[cursor].tokens <= overlap:
            overlap_tokens += sentences[cursor].tokens
            cursor -= 1
        next_start = max(cursor + 1, start + 1)
        start = next_start

    if len(raw_chunks) > 1:
        filtered = [chunk for chunk in raw_chunks if chunk.token_count >= MIN_CHUNK_TOKENS]
        if filtered:
            raw_chunks = filtered
    return raw_chunks


def _segments_for_page(
    text: str,
    headings: tuple[Heading, ...],
) -> list[tuple[str | None, str]]:
    """Split one page into ``(heading, text)`` segments.

    ``heading`` is ``None`` for text that continues the current section.
    """
    if not text:
        return []

    positions = sorted(
        (heading for heading in headings if 0 <= heading.char_offset <= len(text)),
        key=lambda heading: heading.char_offset,
    )
    if not positions:
        return [(None, text)]

    segments: list[tuple[str | None, str]] = []
    first_offset = positions[0].char_offset
    if first_offset > 0:
        segments.append((None, text[:first_offset]))

    for index, heading in enumerate(positions):
        end = positions[index + 1].char_offset if index + 1 < len(positions) else len(text)
        body = text[heading.char_offset : end]
        if body.startswith(heading.text):
            body = body[len(heading.text) :]
        body = body.strip()
        segments.append((heading.text, body))
    return segments


def chunk_document(
    document: ExtractedDocument,
    token_counter: TokenCounter,
    *,
    max_tokens: int = 600,
    overlap: int = 90,
) -> list[ChunkData]:
    """Chunk an extracted document into sentence-aligned windows."""
    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive")
    if overlap < 0:
        raise ValueError("overlap must not be negative")

    sections: list[_Section] = []
    current: _Section | None = None

    def flush() -> None:
        nonlocal current
        if current is not None and current.pieces:
            sections.append(current)
        current = None

    for page in document.pages:
        for heading_text, body in _segments_for_page(page.text, page.headings):
            if heading_text is not None:
                flush()
                current = _Section(title=heading_text, pieces=[])
                if body:
                    current.pieces.append((body, page.page_no))
                continue

            if body:
                if current is None:
                    current = _Section(title=None, pieces=[])
                current.pieces.append((body, page.page_no))

    flush()

    if not sections:
        return []

    chunks: list[ChunkData] = []
    chunk_index = 0
    for section in sections:
        title = section.title
        if title is None:
            title = FALLBACK_SECTION_TITLE if len(sections) == 1 else INTRODUCTION_TITLE
        section.title = _normalize(title)[:255]
        for chunk in _pack_section(section, token_counter, max_tokens, overlap):
            chunks.append(
                ChunkData(
                    chunk_index=chunk_index,
                    text=chunk.text,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                    section_title=chunk.section_title,
                    token_count=chunk.token_count,
                )
            )
            chunk_index += 1
    return chunks
