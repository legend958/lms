"""Export Postgres chunks and their Chroma vectors as a readable text file.

Chunks live in the ``chunks`` table; the MiniLM embeddings live in Chroma
(collection ``lms_chunks``). This script joins them by ``vector_id`` and writes
one human-readable block per chunk, so the segmentation and the vectors can be
inspected without a database client.

Run it inside the compose network (the API container can reach Postgres and
Chroma):

    docker compose cp scripts/export_chunks.py lms-api-1:/tmp/export_chunks.py
    docker compose exec -T api sh -c \
      "cd /app && PYTHONPATH=/app python /tmp/export_chunks.py --output /tmp/chunks.txt"

Then copy the result out:

    docker compose cp lms-api-1:/tmp/chunks.txt data/chunks-and-embeddings.txt

The output is generated evidence, not application state; it can be deleted and
regenerated at any time.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import os
from collections import Counter
from typing import Any

import chromadb
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

QUERY = text(
    """
    SELECT d.filename,
           c.vector_id,
           c.chunk_index,
           c.page_start,
           c.page_end,
           c.section_title,
           c.token_count,
           c.released,
           c.text,
           t.title AS topic_title,
           t.release_state AS topic_release_state
    FROM chunks c
    JOIN documents d ON d.id = c.document_id
    LEFT JOIN topics t ON t.id = c.topic_id
    ORDER BY d.filename, c.chunk_index
    """
)


def _vector(values: Any) -> list[float]:
    # Chroma returns numpy arrays in some versions and plain lists in others.
    if hasattr(values, "tolist"):
        values = values.tolist()
    return [float(value) for value in values]


def _format_vector(values: list[float], per_line: int = 8) -> str:
    lines = []
    for start in range(0, len(values), per_line):
        window = values[start : start + per_line]
        lines.append("    " + ", ".join(f"{value: .5f}" for value in window))
    return "\n".join(lines)


async def _load_rows(database_url: str) -> list[Any]:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(QUERY)
            return list(result.mappings())
    finally:
        await engine.dispose()


def _load_embeddings() -> dict[str, list[float]]:
    client = chromadb.HttpClient(
        host=os.environ.get("CHROMA_HOST", "localhost"),
        port=int(os.environ.get("CHROMA_PORT", "8001")),
    )
    collection = client.get_collection(
        name=os.environ.get("CHROMA_COLLECTION", "lms_chunks")
    )
    payload = collection.get(include=["embeddings"])
    ids = payload.get("ids") or []
    embeddings = payload.get("embeddings")
    if embeddings is None:
        return {}
    return {
        vector_id: _vector(values)
        for vector_id, values in zip(ids, embeddings, strict=True)
    }


def write_report(
    rows: list[Any],
    embeddings: dict[str, list[float]],
    *,
    output: str,
    model: str,
    dimension: int,
) -> Counter[str]:
    per_document: Counter[str] = Counter(row["filename"] for row in rows)
    missing = [row["vector_id"] for row in rows if row["vector_id"] not in embeddings]
    generated = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%M:%SZ")

    with open(output, "w", encoding="utf-8") as handle:
        handle.write("LMS chunk + embedding export\n")
        handle.write("=" * 78 + "\n")
        handle.write(f"generated:        {generated}\n")
        handle.write(f"embedding model:  {model} ({dimension} dimensions)\n")
        handle.write(f"chunks in Postgres: {len(rows)}\n")
        handle.write(f"vectors in Chroma:  {len(embeddings)}\n")
        if missing:
            handle.write(
                f"vectors missing:    {len(missing)} ({', '.join(missing[:5])})\n"
            )
        handle.write("\ndocuments:\n")
        for filename, count in sorted(per_document.items()):
            document_rows = [row for row in rows if row["filename"] == filename]
            released = sum(1 for row in document_rows if row["released"])
            pages = (
                min(row["page_start"] for row in document_rows),
                max(row["page_end"] for row in document_rows),
            )
            handle.write(
                f"  {filename}: {count} chunks, pages {pages[0]}-{pages[1]}, "
                f"{released} released to students\n"
            )
        handle.write("\n")

        current = None
        for row in rows:
            if row["filename"] != current:
                current = row["filename"]
                handle.write("=" * 78 + "\n")
                handle.write(f"DOCUMENT {current}\n")
                handle.write("=" * 78 + "\n\n")

            vector = embeddings.get(row["vector_id"])
            handle.write("-" * 78 + "\n")
            handle.write(
                f"chunk {row['chunk_index']:>3}  vector_id={row['vector_id']}  "
                f"pages={row['page_start']}-{row['page_end']}  "
                f"tokens={row['token_count']}  released={str(row['released']).lower()}\n"
            )
            handle.write(f"section: {row['section_title'] or '(none)'}\n")
            handle.write(
                f"topic:   {row['topic_title'] or '(unassigned)'} "
                f"[{row['topic_release_state'] or 'n/a'}]\n"
            )
            handle.write("text:\n")
            handle.writelines(f"  {line}\n" for line in row["text"].splitlines())
            handle.write("\n")
            if vector is None:
                handle.write("embedding: MISSING in Chroma\n\n")
                continue
            norm = sum(value * value for value in vector) ** 0.5
            handle.write(f"embedding: {len(vector)} dims, L2 norm={norm:.5f}\n")
            handle.write(_format_vector(vector))
            handle.write("\n\n")

    return per_document


def write_texts(rows: list[Any], *, text_dir: str) -> None:
    """Write the extracted chunk text per document, without vectors."""
    os.makedirs(text_dir, exist_ok=True)
    by_document: dict[str, list[Any]] = {}
    for row in rows:
        by_document.setdefault(row["filename"], []).append(row)

    for filename, document_rows in by_document.items():
        path = os.path.join(text_dir, os.path.splitext(filename)[0] + ".txt")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(f"# Extracted text: {filename}\n")
            handle.write(f"# chunks: {len(document_rows)}\n\n")
            for row in document_rows:
                handle.write("-" * 78 + "\n")
                handle.write(
                    f"chunk {row['chunk_index']} | "
                    f"pages {row['page_start']}-{row['page_end']} | "
                    f"tokens {row['token_count']} | "
                    f"released {str(row['released']).lower()}\n"
                )
                handle.write(f"section: {row['section_title'] or '(none)'}\n")
                handle.write(f"topic: {row['topic_title'] or '(unassigned)'}\n\n")
                handle.write(row["text"].strip() + "\n\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default="/tmp/chunks-and-embeddings.txt",
        help="path to write (default: /tmp/chunks-and-embeddings.txt)",
    )
    parser.add_argument("--model", default=os.environ.get("EMBEDDING_MODEL", ""))
    parser.add_argument(
        "--dimension", type=int, default=int(os.environ.get("EMBEDDING_DIM", "384"))
    )
    parser.add_argument(
        "--text-dir",
        default="",
        help="optional directory for per-document extracted text (no vectors)",
    )
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is not set; run this inside the api container")

    rows = asyncio.run(_load_rows(database_url))
    embeddings = _load_embeddings()
    model = args.model or "sentence-transformers/all-MiniLM-L6-v2"
    per_document = write_report(
        rows,
        embeddings,
        output=args.output,
        model=model,
        dimension=args.dimension,
    )

    print(f"wrote {args.output}")
    print(f"chunks: {len(rows)}  vectors: {len(embeddings)}")
    for filename, count in sorted(per_document.items()):
        print(f"  {filename}: {count} chunks")
    if args.text_dir:
        write_texts(rows, text_dir=args.text_dir)
        print(f"wrote extracted text to {args.text_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
