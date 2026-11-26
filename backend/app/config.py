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

    # The switches STRUCTURED_INGEST_ENABLED, PDF_STRUCTURED_INGEST_ENABLED and
    # AGENTIC_SEARCH_ENABLED went on 2026-11-26: PDF uploads and the research
    # agent are always on (every stack set them true). An old .env that still
    # sets them is ignored (extra="ignore").
    # New classes start in exam mode (no course file until the teacher allows
    # some): the safe start for teachers coming from v6. The teacher turns it off.
    NEW_CLASS_EXAM_MODE: bool = True
    # M2-C: model descriptions of pictures, on a teacher's request only. Turn on
    # only after the configured VLM_MODEL was checked to really read images.
    STRUCTURED_VISION_ENABLED: bool = False
    # The SearXNG instance of the web search tool (agent/tools/web.py), e.g.
    # http://searxng:8080 on the compose network. Empty: no web search; a
    # question that asks for it is refused (503 web_search_not_configured).
    WEB_SEARCH_URL: str = ""
    # A summary of the older part of a long conversation instead of dropping it
    # (agent/context/compaction.py, ported from Open WebUI).
    CONTEXT_COMPACTION_ENABLED: bool = True

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/edudb"

    # JWT
    JWT_SECRET: str = "change-me-in-production"
    # The password an admin reset gives; the user must change it at the next sign-in.
    RESET_PASSWORD: str = "123456"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 8  # 8 hours

    # LLM proxy
    # Used only while system_configs has no value. A local port for a
    # vLLM server. The real values are set in the admin page.
    LLM_BASE_URL: str = "http://localhost:8001/v1"
    LLM_API_KEY: str = "EMPTY"
    LLM_MODEL: str = "flash-next"

    # Where uploaded course documents are stored (mounted Docker volume)
    DOCUMENTS_STORAGE_ROOT: str = "/data/documents"


settings = Settings()
