"""Grounded answer generation with deterministic citation validation.

The model is never trusted on its own. Every answer is validated against the
chunks that were actually retrieved: citation markers must point at a real
context block, and an answer with no valid citation is downgraded to the
refusal response. Generation is deliberately non-streaming so the full text is
available for that validation before anything is sent to the student.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import structlog

from app.core.config import Settings, get_settings
from app.rag.groq_retry import retry_delay
from app.rag.retriever import RetrievedChunk

logger = structlog.get_logger(__name__)

PROMPT_PATH = Path(__file__).with_name("prompts") / "answer_v1.txt"
REFUSAL_TEXT = "This isn't covered in your released material."
CITATION_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")
# Models occasionally mirror CJK typography even in English answers and emit
# ``【1】`` or ``［1］``. Fold those variants to ASCII so a correct, grounded
# answer is not downgraded to a refusal by the citation validator.
CITATION_BRACKET_TRANSLATION = str.maketrans(
    {
        "【": "[",
        "】": "]",
        "［": "[",
        "］": "]",
        "〔": "[",
        "〕": "]",
        "❲": "[",
        "❳": "]",
        "⁅": "[",
        "⁆": "]",
    }
)
SPACE_BEFORE_PUNCTUATION_RE = re.compile(r"[ \t]+([.,;:!?])")
INJECTION_RE = re.compile(
    r"(?:ignore|disregard|forget)\s+(?:all\s+)?(?:the\s+)?(?:previous|prior|above|earlier)\s+"
    r"(?:instructions?|prompts?|rules?)"
    r"|system\s+prompt"
    r"|reveal\s+(?:your|the)\s+(?:prompt|instructions)"
    r"|you\s+are\s+now\b"
    r"|act\s+as\s+(?:a|an|the)\b"
    r"|jailbreak"
    r"|developer\s+mode",
    re.IGNORECASE,
)


class AnswerGenerationUnavailable(RuntimeError):
    """Raised when the LLM cannot produce an answer after retries."""


@dataclass(frozen=True)
class Citation:
    document_id: uuid.UUID
    title: str
    page: int
    chunk_id: uuid.UUID


@dataclass(frozen=True)
class GeneratedAnswer:
    answer: str
    citations: tuple[Citation, ...]
    refusal: bool
    injection_flag: bool
    tokens_in: int
    tokens_out: int


class AnswerGenerator:
    """Protocol-style base for the fake/local generators used in tests."""

    async def answer(
        self,
        question: str,
        chunks: Sequence[RetrievedChunk],
    ) -> GeneratedAnswer:  # pragma: no cover - overridden
        raise NotImplementedError


def detect_injection(chunks: Sequence[RetrievedChunk]) -> bool:
    """Flag chunks that contain imperative meta-instructions.

    The chunk is still usable as context; the flag exists so the query can be
    reviewed later. It must never change what is released.
    """
    return any(INJECTION_RE.search(chunk.text) for chunk in chunks)


def _escape_attribute(value: str) -> str:
    return (
        value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
    )


def _block(index: int, chunk: RetrievedChunk) -> str:
    section = ""
    if chunk.section_title:
        section = f' section="{_escape_attribute(chunk.section_title)}"'
    # Never let source text close the context element and inject new markup.
    text = chunk.text.replace("</ctx", "&lt;/ctx")
    return (
        f'<ctx id="{index}" doc="{_escape_attribute(chunk.title)}" '
        f'page="{chunk.page}"{section}>\n{text}\n</ctx>'
    )


def build_user_prompt(question: str, chunks: Sequence[RetrievedChunk]) -> str:
    blocks = "\n\n".join(_block(index, chunk) for index, chunk in enumerate(chunks, start=1))
    return (
        "Source excerpts:\n\n"
        f"{blocks}\n\n"
        f"Question: {question}\n\n"
        "Answer using only the source excerpts above. Cite every claim with [n]. "
        f'If the answer is not in the excerpts, reply exactly: "{REFUSAL_TEXT}" '
        "and cite nothing."
    )


@lru_cache(maxsize=1)
def system_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8").strip()


def _estimated_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def validate_citations(
    text: str,
    chunks: Sequence[RetrievedChunk],
) -> tuple[str, tuple[Citation, ...], bool]:
    """Remove invalid markers and return the answer, citations, and refusal flag."""
    text = text.translate(CITATION_BRACKET_TRANSLATION)
    limit = len(chunks)
    parts: list[str] = []
    cursor = 0
    ordered: dict[int, Citation] = {}

    for match in CITATION_RE.finditer(text):
        parts.append(text[cursor : match.start()])
        numbers: list[int] = []
        for raw in match.group(1).split(","):
            try:
                number = int(raw.strip())
            except ValueError:  # pragma: no cover - regex already guarantees digits
                continue
            if 1 <= number <= limit and number not in numbers:
                numbers.append(number)
        for number in numbers:
            chunk = chunks[number - 1]
            ordered.setdefault(
                number,
                Citation(
                    document_id=chunk.document_id,
                    title=chunk.title,
                    page=chunk.page,
                    chunk_id=chunk.chunk_id,
                ),
            )
        parts.append("".join(f"[{number}]" for number in numbers))
        cursor = match.end()
    parts.append(text[cursor:])

    cleaned = "".join(parts)
    cleaned = SPACE_BEFORE_PUNCTUATION_RE.sub(r"\1", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    citations = tuple(ordered.values())
    if not cleaned or not citations:
        return REFUSAL_TEXT, (), True
    return cleaned, citations, False


class GroqAnswerGenerator(AnswerGenerator):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def answer(
        self,
        question: str,
        chunks: Sequence[RetrievedChunk],
    ) -> GeneratedAnswer:
        if not chunks:
            return GeneratedAnswer(
                answer=REFUSAL_TEXT,
                citations=(),
                refusal=True,
                injection_flag=False,
                tokens_in=0,
                tokens_out=0,
            )
        if not self.settings.groq_api_key:
            raise AnswerGenerationUnavailable("GROQ_API_KEY is not configured")

        injection_flag = detect_injection(chunks)
        if injection_flag:
            logger.warning("assistant_injection_flagged", chunk_count=len(chunks))

        content, tokens_in, tokens_out = await self._request(question, chunks)
        answer, citations, refusal = validate_citations(content, chunks)
        return GeneratedAnswer(
            answer=answer,
            citations=citations,
            refusal=refusal,
            injection_flag=injection_flag,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )

    async def _request(
        self,
        question: str,
        chunks: Sequence[RetrievedChunk],
    ) -> tuple[str, int, int]:
        try:
            from groq import AsyncGroq
        except ImportError as exc:  # pragma: no cover - dependency is required
            raise AnswerGenerationUnavailable("The Groq SDK is not installed") from exc

        prompt = build_user_prompt(question, chunks)
        last_error: Exception | None = None
        attempts = max(1, self.settings.groq_answer_retries)
        request_options: dict[str, Any] = {}
        if self.settings.groq_reasoning_effort is not None:
            request_options["reasoning_effort"] = self.settings.groq_reasoning_effort

        for attempt in range(1, attempts + 1):
            client = AsyncGroq(
                api_key=self.settings.groq_api_key,
                timeout=60.0,
                max_retries=0,
            )
            try:
                completion = await client.chat.completions.create(
                    model=self.settings.groq_model,
                    messages=[
                        {"role": "system", "content": system_prompt()},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=self.settings.groq_temperature,
                    max_tokens=self.settings.groq_max_tokens,
                    **request_options,
                )
                content = completion.choices[0].message.content
                if not content or not content.strip():
                    raise AnswerGenerationUnavailable("The model returned an empty response")

                usage = getattr(completion, "usage", None)
                prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
                completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
                if not prompt_tokens:
                    prompt_tokens = _estimated_tokens(system_prompt() + prompt)
                if not completion_tokens:
                    completion_tokens = _estimated_tokens(content)
                return content.strip(), prompt_tokens, completion_tokens
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "assistant_generation_attempt_failed",
                    attempt=attempt,
                    error=str(exc),
                )
                if attempt < attempts:
                    await asyncio.sleep(retry_delay(exc, attempt))
            finally:
                close = getattr(client, "close", None)
                if close is not None:
                    await close()

        raise AnswerGenerationUnavailable("Answer generation failed after retries") from last_error


@lru_cache(maxsize=1)
def get_answer_generator() -> AnswerGenerator:
    return GroqAnswerGenerator(get_settings())
