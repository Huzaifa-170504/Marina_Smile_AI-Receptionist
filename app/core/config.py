from functools import lru_cache
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import Field, HttpUrl, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    supabase_url: HttpUrl = Field(alias="SUPABASE_URL")
    supabase_secret_key: SecretStr = Field(alias="SUPABASE_SECRET_KEY")
    gemini_api_key: SecretStr = Field(alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-3.5-flash-lite", alias="GEMINI_MODEL")
    gemini_embedding_model: Literal["gemini-embedding-2"] = Field(
        default="gemini-embedding-2",
        alias="GEMINI_EMBEDDING_MODEL",
    )
    gemini_embedding_dimensions: int = Field(
        default=768,
        alias="GEMINI_EMBEDDING_DIMENSIONS",
    )

    default_clinic_id: UUID = Field(
        default=UUID("10000000-0000-4000-8000-000000000001"),
        alias="DEFAULT_CLINIC_ID",
    )
    rag_match_count: int = Field(default=5, ge=1, alias="RAG_MATCH_COUNT")
    rag_match_threshold: float = Field(
        default=0.70,
        ge=0.0,
        le=1.0,
        alias="RAG_MATCH_THRESHOLD",
    )
    rag_chunk_size: int = Field(default=900, ge=1, alias="RAG_CHUNK_SIZE")
    rag_chunk_overlap: int = Field(default=150, ge=0, alias="RAG_CHUNK_OVERLAP")
    availability_slot_interval_minutes: int = Field(
        default=15,
        ge=1,
        alias="AVAILABILITY_SLOT_INTERVAL_MINUTES",
    )
    generic_availability_minutes: int = Field(
        default=30,
        ge=1,
        alias="GENERIC_AVAILABILITY_MINUTES",
    )
    availability_search_days: int = Field(
        default=30,
        ge=1,
        le=90,
        alias="AVAILABILITY_SEARCH_DAYS",
    )
    google_calendar_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        le=120,
        alias="GOOGLE_CALENDAR_TIMEOUT_SECONDS",
    )
    google_application_credentials: Path | None = Field(
        default=None,
        alias="GOOGLE_APPLICATION_CREDENTIALS",
    )
    dashboard_allowed_origins: str = Field(
        default="",
        alias="DASHBOARD_ALLOWED_ORIGINS",
    )
    dashboard_application_url: HttpUrl = Field(
        default="http://localhost:3000",
        alias="DASHBOARD_APPLICATION_URL",
    )
    demo_enabled: bool = Field(default=False, alias="DEMO_ENABLED")
    demo_api_token: SecretStr | None = Field(default=None, alias="DEMO_API_TOKEN")
    demo_query_limit: int = Field(default=10, ge=1, le=100, alias="DEMO_QUERY_LIMIT")
    demo_query_window_seconds: int = Field(
        default=3600,
        ge=60,
        le=86400,
        alias="DEMO_QUERY_WINDOW_SECONDS",
    )

    @field_validator(
        "supabase_secret_key",
        "gemini_api_key",
        mode="after",
    )
    @classmethod
    def validate_secret(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("must not be empty")
        return value

    @field_validator("gemini_embedding_dimensions", mode="after")
    @classmethod
    def validate_embedding_dimensions(cls, value: int) -> int:
        if value != 768:
            raise ValueError("GEMINI_EMBEDDING_DIMENSIONS must be exactly 768")
        return value

    @field_validator("dashboard_application_url", mode="after")
    @classmethod
    def validate_dashboard_application_url(cls, value: HttpUrl) -> HttpUrl:
        if value.path not in (None, "", "/") or value.query or value.fragment:
            raise ValueError("DASHBOARD_APPLICATION_URL must be an origin without a path, query, or fragment")
        return value

    @model_validator(mode="after")
    def validate_chunk_configuration(self) -> "Settings":
        if self.rag_chunk_overlap >= self.rag_chunk_size:
            raise ValueError("RAG_CHUNK_OVERLAP must be smaller than RAG_CHUNK_SIZE")
        if self.demo_enabled:
            token = self.demo_api_token.get_secret_value() if self.demo_api_token else ""
            if len(token.strip()) < 32:
                raise ValueError("DEMO_API_TOKEN must contain at least 32 characters when DEMO_ENABLED is true")
        return self

    @property
    def dashboard_origins(self) -> list[str]:
        return [
            origin.strip().rstrip("/")
            for origin in self.dashboard_allowed_origins.split(",")
            if origin.strip()
        ]

    @property
    def dashboard_url(self) -> str:
        return str(self.dashboard_application_url).rstrip("/")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
