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


settings = Settings()
