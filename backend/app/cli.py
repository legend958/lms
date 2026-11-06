"""Operational CLI for ingestion and vector inspection."""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import DomainError
from app.core.roles import CourseRole, Role
from app.core.scope import CourseScope
from app.db.models import User
from app.db.repositories import courses as courses_repo
from app.db.repositories import documents as documents_repo
from app.db.repositories import topics as topics_repo
from app.db.repositories import users as users_repo
from app.db.session import dispose_engine, get_sessionmaker, init_engine
from app.rag.vector_store import VectorStoreError, get_vector_store
from app.services.document_service import upload_document
from app.services.ingestion_service import IngestError, reset_for_retry, run_ingest
from app.services.storage import get_storage


async def _scope_for_user(session: AsyncSession, user: User) -> CourseScope:
    if user.role is Role.ADMIN:
        course_ids = await courses_repo.list_all_ids(session)
    elif user.role is Role.TEACHER:
        taught = await courses_repo.list_course_ids_for_user(
            session, user.id, course_role=CourseRole.TEACHER
        )
        created = await courses_repo.list_created_ids(session, user.id)
        course_ids = taught | created
    else:
        course_ids = await courses_repo.list_course_ids_for_user(session, user.id)
    return CourseScope(user_id=user.id, role=user.role, course_ids=frozenset(course_ids))


async def _ingest(args: argparse.Namespace) -> int:
    init_engine()
    try:
        path = Path(args.path)
        if not await asyncio.to_thread(path.is_file):
            print(f"File not found: {path}", file=sys.stderr)
            return 2
        max_bytes = get_settings().max_upload_mb * 1024 * 1024
        size = await asyncio.to_thread(lambda: path.stat().st_size)
        if size > max_bytes:
            print(f"PDF exceeds the {get_settings().max_upload_mb} MB limit", file=sys.stderr)
            return 2
        data = await asyncio.to_thread(path.read_bytes)
        storage = get_storage()

        async with get_sessionmaker()() as session:
            user = await users_repo.get_by_email(session, args.user_email)
            if user is None:
                print(f"User not found: {args.user_email}", file=sys.stderr)
                return 2
            scope = await _scope_for_user(session, user)
            if user.role not in {Role.TEACHER, Role.ADMIN}:
                print("Only teachers and admins can ingest documents", file=sys.stderr)
                return 2
            try:
                document = await upload_document(
                    session,
                    actor=user,
                    scope=scope,
                    course_id=args.course_id,
                    filename=path.name,
                    content_type="application/pdf",
                    data=data,
                    storage=storage,
                )
            except DomainError as exc:
                print(f"Upload failed: {exc.detail}", file=sys.stderr)
                return 1
            document_id = document.id
            print(f"Uploaded {path.name} as {document_id}")

        try:
            await run_ingest(document_id, storage=storage)
        except IngestError as exc:
            print(f"Ingestion failed: {exc.code.value}: {exc.detail}", file=sys.stderr)
            return 1
        async with get_sessionmaker()() as session:
            stored = await documents_repo.get(session, document_id)
            if stored is None:
                print("Document disappeared after ingestion", file=sys.stderr)
                return 1
            chunk_count = await documents_repo.count_chunks(session, document_id)
            topic_count = len(await topics_repo.list_for_document(session, document_id))
            print(
                f"status={stored.status.value} pages={stored.page_count} "
                f"chunks={chunk_count} topics={topic_count} "
                f"topics_source={stored.topics_source.value if stored.topics_source else 'unknown'}"
            )
            if stored.error_code:
                print(f"error={stored.error_code}: {stored.error_detail}", file=sys.stderr)
                return 1
        return 0
    finally:
        await dispose_engine()


async def _reindex(args: argparse.Namespace) -> int:
    init_engine()
    try:
        async with get_sessionmaker()() as session:
            document = await documents_repo.get(session, args.document_id)
            if document is None:
                print(f"Document not found: {args.document_id}", file=sys.stderr)
                return 2
        await reset_for_retry(args.document_id)
        try:
            await run_ingest(args.document_id)
        except IngestError as exc:
            print(f"Ingestion failed: {exc.code.value}: {exc.detail}", file=sys.stderr)
            return 1
        async with get_sessionmaker()() as session:
            stored = await documents_repo.get(session, args.document_id)
            if stored is None:
                print("Document disappeared after reindex", file=sys.stderr)
                return 1
            chunk_count = await documents_repo.count_chunks(session, args.document_id)
            topic_count = len(await topics_repo.list_for_document(session, args.document_id))
            print(
                f"status={stored.status.value} pages={stored.page_count} "
                f"chunks={chunk_count} topics={topic_count} "
                f"topics_source={stored.topics_source.value if stored.topics_source else 'unknown'}"
            )
            if stored.error_code:
                print(f"error={stored.error_code}: {stored.error_detail}", file=sys.stderr)
                return 1
        return 0
    finally:
        await dispose_engine()


async def _vector_stats(args: argparse.Namespace) -> int:
    vector_store = get_vector_store()
    try:
        if args.topic_id:
            result = vector_store.get(where={"topic_id": str(args.topic_id)})
            ids = list(result.get("ids") or [])
            metadatas = list(result.get("metadatas") or [])
            released = sum(1 for item in metadatas if item.get("released") is True)
            print(
                f"topic_id={args.topic_id} vectors={len(ids)} "
                f"released={released} hidden={len(ids) - released}"
            )
        elif args.document_id:
            count = vector_store.count(where={"document_id": str(args.document_id)})
            print(f"document_id={args.document_id} vectors={count}")
        else:
            print(f"vectors={vector_store.count()}")
    except VectorStoreError as exc:
        print(f"Vector store unavailable: {exc}", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest = subparsers.add_parser("ingest", help="Upload and ingest a PDF synchronously")
    ingest.add_argument("path", type=Path)
    ingest.add_argument("--course-id", type=uuid.UUID, required=True)
    ingest.add_argument("--user-email", required=True)
    ingest.set_defaults(func=_ingest)

    reindex = subparsers.add_parser("reindex", help="Re-run ingestion for a document")
    reindex.add_argument("document_id", type=uuid.UUID)
    reindex.set_defaults(func=_reindex)

    stats = subparsers.add_parser("vector-stats", help="Show vector collection counts")
    stats.add_argument("--document-id", type=uuid.UUID, default=None)
    stats.add_argument(
        "--topic",
        dest="topic_id",
        type=uuid.UUID,
        default=None,
        help="Inspect one topic's vectors and release flags",
    )
    stats.set_defaults(func=_vector_stats)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    raise SystemExit(asyncio.run(args.func(args)))


if __name__ == "__main__":
    main()
