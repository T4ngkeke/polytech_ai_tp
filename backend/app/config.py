"""
config.py — Centralised settings loaded from the .env file via pydantic-settings.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file="backend/.env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Structured ingestion and the research agent remain opt-in during development.
    STRUCTURED_INGEST_ENABLED: bool = False
    PDF_STRUCTURED_INGEST_ENABLED: bool = False
    AGENTIC_SEARCH_ENABLED: bool = False
    # New classes start in exam mode (Direct answers, no course search): the
    # safe start for teachers coming from v6. The teacher turns it off.
    NEW_CLASS_EXAM_MODE: bool = True
    # M2-C: model descriptions of pictures, on a teacher's request only. Turn on
    # only after the configured VLM_MODEL was checked to really read images.
    STRUCTURED_VISION_ENABLED: bool = False

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/edudb"

    # JWT
    JWT_SECRET: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 8  # 8 hours

    # LLM proxy
    LLM_BASE_URL: str = "http://localhost:11434/v1"  # Ollama default
    LLM_API_KEY: str = "ollama"                       # Placeholder
    LLM_MODEL: str = "qwen3.5:0.8b"                        # Default model

    # [v7] Where uploaded course documents are stored (mounted Docker volume)
    DOCUMENTS_STORAGE_ROOT: str = "/data/documents"


settings = Settings()
