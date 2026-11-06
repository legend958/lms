"""documents, ingestion jobs, and chunks

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

document_status = sa.Enum(
    "queued",
    "extracting",
    "chunking",
    "embedding",
    "indexed",
    "topic_extraction",
    "ready",
    "failed",
    name="document_status",
    native_enum=False,
)
ingest_stage = sa.Enum(
    "queued",
    "extracting",
    "chunking",
    "embedding",
    "indexed",
    "failed",
    name="ingest_stage",
    native_enum=False,
)


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("course_id", sa.Uuid(), nullable=False),
        sa.Column("uploaded_by", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("status", document_status, nullable=False),
        sa.Column("error_code", sa.String(length=40), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("pipeline_version", sa.Integer(), nullable=False),
        sa.Column("topic_count", sa.Integer(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["uploaded_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_documents_course_id", "documents", ["course_id"])
    op.create_index("ix_documents_course_status", "documents", ["course_id", "status"])
    op.create_index(
        "uq_documents_course_checksum_active",
        "documents",
        ["course_id", "checksum"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
        sqlite_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "ingestion_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("stage", ingest_stage, nullable=False),
        sa.Column("progress_pct", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ingestion_jobs_document_id", "ingestion_jobs", ["document_id"])
    op.create_index(
        "ix_ingestion_jobs_document_created",
        "ingestion_jobs",
        ["document_id", "created_at"],
    )

    op.create_table(
        "chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("vector_id", sa.String(length=80), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("course_id", sa.Uuid(), nullable=False),
        sa.Column("topic_id", sa.Uuid(), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=False),
        sa.Column("page_end", sa.Integer(), nullable=False),
        sa.Column("section_title", sa.String(length=255), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("released", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "chunk_index", name="uq_chunks_document_index"),
        sa.UniqueConstraint("vector_id"),
    )
    op.create_index("ix_chunks_document_id", "chunks", ["document_id"])
    op.create_index("ix_chunks_course_id", "chunks", ["course_id"])
    op.create_index("ix_chunks_topic_id", "chunks", ["topic_id"])
    op.create_index("ix_chunks_released", "chunks", ["released"])
    op.create_index("ix_chunks_course_released", "chunks", ["course_id", "released"])


def downgrade() -> None:
    op.drop_index("ix_chunks_course_released", table_name="chunks")
    op.drop_index("ix_chunks_released", table_name="chunks")
    op.drop_index("ix_chunks_topic_id", table_name="chunks")
    op.drop_index("ix_chunks_course_id", table_name="chunks")
    op.drop_index("ix_chunks_document_id", table_name="chunks")
    op.drop_table("chunks")
    op.drop_index("ix_ingestion_jobs_document_created", table_name="ingestion_jobs")
    op.drop_index("ix_ingestion_jobs_document_id", table_name="ingestion_jobs")
    op.drop_table("ingestion_jobs")
    op.drop_index("uq_documents_course_checksum_active", table_name="documents")
    op.drop_index("ix_documents_course_status", table_name="documents")
    op.drop_index("ix_documents_course_id", table_name="documents")
    op.drop_table("documents")
