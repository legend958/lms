"""PDF text and heading extraction using PyMuPDF.

The extractor deliberately keeps page boundaries and heading offsets. The
chunker uses those offsets to split sections without guessing where a heading
appears inside a page.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

try:  # PyMuPDF exposes both module names depending on the installed version.
    import pymupdf as fitz
except ImportError:  # pragma: no cover - older PyMuPDF
    import fitz  # type: ignore[no-redef]


MIN_DOCUMENT_TEXT_CHARS = 50
MIN_PAGE_TEXT_CHARS = 20
MAX_HEADING_CHARS = 180


class PdfExtractionError(RuntimeError):
    """Base class for typed extraction failures."""


class EncryptedPdfError(PdfExtractionError):
    """The PDF requires a password."""


class PdfParseError(PdfExtractionError):
    """The PDF is corrupt or cannot be parsed."""


@dataclass(frozen=True, slots=True)
class Heading:
    level: int
    text: str
    page_no: int
    char_offset: int


@dataclass(frozen=True, slots=True)
class Page:
    page_no: int
    text: str
    headings: tuple[Heading, ...]
    is_image_only: bool


@dataclass(frozen=True, slots=True)
class PdfInfo:
    page_count: int


@dataclass(frozen=True, slots=True)
class ExtractedDocument:
    page_count: int
    pages: tuple[Page, ...]

    @property
    def text(self) -> str:
        return "\n\n".join(page.text for page in self.pages if page.text)

    @property
    def has_text(self) -> bool:
        return len(self.text.strip()) >= MIN_DOCUMENT_TEXT_CHARS

    @property
    def image_only_pages(self) -> tuple[int, ...]:
        return tuple(page.page_no for page in self.pages if page.is_image_only)


@dataclass(slots=True)
class _Block:
    text: str
    max_size: float
    bold: bool
    char_count: int
    y0: float
    y1: float


def _clean_line(value: str) -> str:
    return re.sub(r"[ \t]+", " ", value).strip()


def _block_from_raw(raw: dict[str, Any]) -> _Block | None:
    lines: list[str] = []
    sizes: list[float] = []
    bold_chars = 0
    char_count = 0
    for line in raw.get("lines", []):
        parts: list[str] = []
        for span in line.get("spans", []):
            text = span.get("text", "")
            if not text:
                continue
            parts.append(text)
            size = float(span.get("size", 0.0))
            sizes.append(size)
            if int(span.get("flags", 0)) & 16:
                bold_chars += len(text)
            char_count += len(text)
        cleaned = _clean_line("".join(parts))
        if cleaned and (not lines or lines[-1].casefold() != cleaned.casefold()):
            lines.append(cleaned)

    text = "\n".join(lines).strip()
    if not text:
        return None
    bbox = raw.get("bbox", (0.0, 0.0, 0.0, 0.0))
    y0 = float(bbox[1]) if len(bbox) > 1 else 0.0
    y1 = float(bbox[3]) if len(bbox) > 3 else y0
    max_size = max(sizes, default=0.0)
    return _Block(
        text=text,
        max_size=max_size,
        bold=bool(char_count and bold_chars / char_count >= 0.6),
        char_count=char_count,
        y0=y0,
        y1=y1,
    )


def _body_font_size(blocks_by_page: list[list[_Block]]) -> float:
    weights: dict[float, int] = {}
    for blocks in blocks_by_page:
        for block in blocks:
            key = round(block.max_size * 2) / 2
            weights[key] = weights.get(key, 0) + block.char_count
    if not weights:
        return 0.0
    return max(weights, key=lambda size: weights[size])


def _heading_level(block: _Block, body_size: float) -> int | None:
    text = block.text
    if len(text) > MAX_HEADING_CHARS or len(text.splitlines()) > 4:
        return None
    if len(text.strip()) < 3 or len(re.findall(r"[A-Za-z]", text)) < 3:
        return None
    if text.strip().isdigit() or not re.search(r"[A-Za-z]", text):
        return None
    if text.rstrip().endswith((".", ";", ",")):
        return None

    size_delta = block.max_size - body_size
    looks_large = size_delta >= 1.5
    looks_bold = block.bold and size_delta >= -0.5
    if not (looks_large or looks_bold):
        return None
    if size_delta >= 8:
        return 1
    if size_delta >= 4:
        return 2
    return 3


def _margin_key(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def _is_margin(block: _Block, page_height: float) -> bool:
    return block.y1 <= page_height * 0.12 or block.y0 >= page_height * 0.88


def _filter_repeated_margins(
    pages: list[tuple[int, list[_Block], float]],
) -> list[tuple[int, list[_Block], float]]:
    if len(pages) < 3:
        return pages

    counts: dict[str, int] = {}
    for _, blocks, height in pages:
        seen: set[str] = set()
        for block in blocks:
            if not _is_margin(block, height):
                continue
            key = _margin_key(block.text)
            if key not in seen:
                counts[key] = counts.get(key, 0) + 1
                seen.add(key)

    threshold = max(3, (len(pages) + 1) // 2)
    repeated = {key for key, count in counts.items() if count >= threshold}
    if not repeated:
        return pages

    filtered: list[tuple[int, list[_Block], float]] = []
    for page_no, blocks, height in pages:
        kept = [
            block
            for block in blocks
            if not (_is_margin(block, height) and _margin_key(block.text) in repeated)
        ]
        filtered.append((page_no, kept, height))
    return filtered


def _open_pdf(data: bytes) -> Any:
    try:
        document = fitz.open(stream=data, filetype="pdf")  # type: ignore[no-untyped-call]
    except Exception as exc:
        raise PdfParseError("The PDF could not be opened or is corrupt") from exc
    if bool(document.needs_pass) or bool(document.is_encrypted):
        document.close()  # type: ignore[no-untyped-call]
        raise EncryptedPdfError("Encrypted PDFs are not supported")
    return document


def inspect_pdf(data: bytes) -> PdfInfo:
    """Validate that bytes are a readable, unencrypted PDF and count pages."""
    document = _open_pdf(data)
    try:
        return PdfInfo(page_count=int(document.page_count))
    finally:
        document.close()


def extract_pdf(data: bytes) -> ExtractedDocument:
    """Extract page text, image-only flags, and relative heading levels."""
    document = _open_pdf(data)

    try:
        raw_pages: list[tuple[int, list[_Block], float]] = []
        for index, page in enumerate(document):
            page_no = index + 1
            try:
                payload = page.get_text("dict")
            except Exception as exc:
                raise PdfParseError(f"Could not read page {page_no}") from exc

            blocks: list[_Block] = []
            for raw in payload.get("blocks", []):
                if raw.get("type", 0) != 0:
                    continue
                block = _block_from_raw(raw)
                if block is not None:
                    blocks.append(block)
            raw_pages.append((page_no, blocks, float(page.rect.height)))

        raw_pages = _filter_repeated_margins(raw_pages)
        body_size = _body_font_size([blocks for _, blocks, _ in raw_pages])
        pages: list[Page] = []
        for page_no, blocks, _ in raw_pages:
            pieces: list[str] = []
            headings: list[Heading] = []
            offset = 0
            for block in blocks:
                if pieces:
                    offset += 2  # the "\n\n" separator
                level = _heading_level(block, body_size)
                if level is not None:
                    headings.append(
                        Heading(
                            level=level,
                            text=block.text,
                            page_no=page_no,
                            char_offset=offset,
                        )
                    )
                pieces.append(block.text)
                offset += len(block.text)

            page_text = "\n\n".join(pieces).strip()
            pages.append(
                Page(
                    page_no=page_no,
                    text=page_text,
                    headings=tuple(headings),
                    is_image_only=len(page_text) < MIN_PAGE_TEXT_CHARS,
                )
            )

        return ExtractedDocument(page_count=document.page_count, pages=tuple(pages))
    finally:
        document.close()
