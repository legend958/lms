"""Application configuration.

Every setting is read from the environment (or a local .env file). Startup
fails fast when a production-critical value is missing or left at a default.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

INSECURE_SECRET = "dev-only-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_env: Literal["development", "test", "staging", "production"] = "development"
    app_name: str = "LMS"
    app_version: str = "0.1.0"
    api_base_url: str = "http://localhost:8000"
    web_base_url: str = "http://localhost:3000"

    # Infrastructure
    database_url: str = "postgresql+asyncpg://lms:lms@localhost:5432/lms"
    redis_url: str | None = None

    # Auth
    session_secret: str = INSECURE_SECRET
    access_token_ttl_min: int = 15
    refresh_token_ttl_days: int = 7
    cookie_secure: bool = False
    cookie_domain: str | None = None
    seed_admin_password: str = "ChangeMe123!"
    login_rate_limit_per_minute: int = 5
    login_rate_limit_per_hour: int = 10

    # CORS
    allowed_origins: str = "http://localhost:3000"

    # Vector store (Phase 2+)
    chroma_host: str = "localhost"
    chroma_port: int = 8001
    chroma_collection: str = "lms_chunks"
    chroma_top_k: int = 12
    chroma_final_k: int = 6
    chroma_min_similarity: float = 0.30

    # Ingestion queue. "auto" uses Redis/ARQ when REDIS_URL is configured and
    # falls back to an in-process task for single-node development.
    ingest_queue: Literal["auto", "arq", "inline", "disabled"] = "auto"

    # Embeddings (Phase 2+)
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_dim: int = 384
    embedding_batch_size: int = 64
    hf_home: str = ""

    # LLM (Phase 3+)
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    groq_temperature: float = 0.2
    groq_max_tokens: int = 1024
    # gpt-oss spends most of its latency on hidden reasoning. Medium effort is
    # the lowest level that reliably cites grounded answers; ``low`` produced
    # false refusals because the model sometimes omitted citations entirely.
    groq_reasoning_effort: Literal["none", "default", "low", "medium", "high"] | None = "medium"
    groq_topic_max_tokens: int = 4096
    groq_topic_batch_tokens: int = 6000
    groq_topic_retries: int = 2
    groq_quiz_max_tokens: int = 4096
    groq_quiz_retries: int = 5
    quiz_target_questions: int = 10
    quiz_min_questions: int = 5
    quiz_generation_rounds: int = 3
    quiz_max_chunk_tokens: int = 8000

    # Study assistant (Phase 6)
    rag_mmr_lambda: float = 0.7
    rag_prompt_version: str = "answer_v1"
    rag_store_questions: bool = False
    rag_max_question_chars: int = 1000
    groq_answer_retries: int = 2

    # Limits
    max_upload_mb: int = 50
    max_files_per_batch: int = 20
    max_pdf_pages: int = 1000
    assistant_queries_per_hour: int = 20
    chunk_tokens: int = 600
    chunk_overlap_tokens: int = 90

    # Storage (Phase 2+)
    storage_backend: Literal["local", "s3"] = "local"
    upload_dir: str = "./data/uploads"
    s3_bucket: str = ""
    s3_region: str = ""

    @field_validator("database_url")
    @classmethod
    def _normalize_database_url(cls, value: str) -> str:
        """Accept hosted-provider URLs that omit the async driver.

        Render (and Heroku) hand out ``postgresql://`` URLs, but SQLAlchemy's
        async engine needs an async driver in the scheme. Normalize the common
        forms so ``DATABASE_URL`` can be pasted straight from the provider.
        """
        value = value.strip()
        if not value:
            raise ValueError("DATABASE_URL must not be empty")
        for scheme in ("postgres://", "postgresql://"):
            if value.startswith(scheme):
                return "postgresql+asyncpg://" + value[len(scheme) :]
        return value

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    def validate_runtime(self) -> None:
        """Reject unsafe production configuration."""
        if self.is_production and self.session_secret == INSECURE_SECRET:
            raise RuntimeError("SESSION_SECRET must be set to a strong value in production")
        if self.is_production and not self.cookie_secure:
            raise RuntimeError("COOKIE_SECURE must be true in production")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_runtime()
    return settings
