from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    application_name: str = "RAG-Application"
    env: str = "development"
    debug: bool = False
    database_url: str
    postgres_password: str
    database_pool_size: int = Field(default=5, ge=1, le=50)
    database_max_overflow: int = Field(default=5, ge=0, le=50)
    database_pool_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    jwt_secret_key: str = Field(min_length=32)
    jwt_algorithm: Literal["HS256"] = "HS256"
    access_token_expire_minutes: int = Field(default=30, ge=1, le=1440)
    document_storage_dir: Path = Path("storage/documents")
    max_document_size_bytes: int = 10 * 1024 * 1024
    document_chunk_size: int = Field(default=1000, ge=1)
    document_chunk_overlap: int = Field(default=150, ge=0)
    document_processing_lease_seconds: int = Field(default=3600, ge=60)
    document_processing_worker_concurrency: int = Field(default=2, ge=1, le=32)
    document_task_soft_time_limit_seconds: int = Field(
        default=1500,
        ge=60,
        le=7200,
    )
    document_task_time_limit_seconds: int = Field(
        default=1800,
        ge=120,
        le=7200,
    )
    document_worker_max_memory_per_child_kb: int = Field(
        default=2_000_000,
        ge=256_000,
    )
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_batch_size: int = Field(default=32, ge=1)
    max_document_text_chars: int = Field(default=5_000_000, ge=1)
    search_default_top_k: int = Field(default=5, ge=1, le=20)
    search_max_top_k: int = Field(default=20, ge=1, le=20)
    search_rank_constant: int = Field(default=60, ge=1)
    max_context_chars: int = Field(default=12000, ge=1000, le=100000)
    redis_url: str = "redis://localhost:6379/0"
    llm_provider: Literal["groq"] = "groq"
    groq_api_key: str | None = None
    llm_model: str = "llama-3.3-70b-versatile"
    llm_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    llm_max_completion_tokens: int = Field(default=800, ge=1, le=8192)
    conversation_history_max_messages: int = Field(default=10, ge=1, le=50)
    conversation_history_max_chars: int = Field(default=8000, ge=1000, le=50000)
    conversation_retention_days: int = Field(default=90, ge=1, le=3650)
    conversation_request_lease_seconds: int = Field(default=900, ge=60, le=3600)
    rate_limit_enabled: bool = True
    metrics_enabled: bool = True
    metrics_retention_seconds: int = Field(default=2592000, ge=86400)
    rate_limit_window_seconds: int = Field(default=60, ge=1, le=3600)
    rate_limit_login_requests: int = Field(default=10, ge=1)
    rate_limit_search_requests: int = Field(default=60, ge=1)
    rate_limit_ask_requests: int = Field(default=20, ge=1)
    rate_limit_upload_requests: int = Field(default=10, ge=1)
    rate_limit_retry_requests: int = Field(default=10, ge=1)
    redis_timeout_seconds: float = Field(default=1.0, gt=0, le=10)
    cors_allowed_origins: list[str] = Field(default_factory=list)
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8"
    )

    @model_validator(mode="after")
    def validate_chunking(self) -> "Settings":
        if self.document_chunk_overlap >= self.document_chunk_size:
            raise ValueError(
                "document_chunk_overlap must be smaller than "
                "document_chunk_size"
            )
        if (
            self.document_task_soft_time_limit_seconds
            >= self.document_task_time_limit_seconds
        ):
            raise ValueError(
                "document_task_soft_time_limit_seconds must be smaller than "
                "document_task_time_limit_seconds"
            )
        return self

    @field_validator("cors_allowed_origins")
    @classmethod
    def reject_wildcard_cors(
        cls,
        origins: list[str],
    ) -> list[str]:
        normalized_origins = []
        for origin in origins:
            parsed = urlsplit(origin)
            if (
                "*" in origin
                or parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
                or parsed.username
                or parsed.password
            ):
                raise ValueError(
                    "cors_allowed_origins must contain explicit origins"
                )
            normalized_origins.append(origin.rstrip("/"))
        return normalized_origins

settings = Settings()