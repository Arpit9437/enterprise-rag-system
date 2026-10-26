from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    application_name: str = "RAG-Application"
    env: str = "development"
    debug: bool = False
    database_url: str
    postgres_password: str
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    document_storage_dir: Path = Path("storage/documents")
    max_document_size_bytes: int = 10 * 1024 * 1024
    redis_url: str = "redis://localhost:6379/0"
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8"
    )

settings = Settings()