"""
Configuration module — loads all env vars via pydantic-settings.
"""
from __future__ import annotations

import functools
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    DEEPSEEK_API_KEY: str = Field(default="", description="DeepSeek API key")
    DEEPSEEK_BASE_URL: str = Field(
        default="https://api.deepseek.com/v1",
        description="DeepSeek OpenAI-compatible API base URL",
    )
    DEEPSEEK_MODEL: str = Field(default="deepseek-chat", description="DeepSeek model name")
    OPENAI_API_KEY: Optional[str] = Field(default=None, description="OpenAI API key (optional fallback)")
    SUPABASE_URL: Optional[str] = Field(default=None, description="Supabase project URL")
    SUPABASE_KEY: Optional[str] = Field(default=None, description="Supabase anon key")
    SESSION_SECRET: str = Field(default="change_me", description="Session secret")
    OUTPUT_DIR: str = Field(default="./outputs", description="Output directory for generated files")
    MAX_FILE_SIZE_MB: int = Field(default=50, description="Maximum upload file size in MB")
    DEFAULT_K_FACTOR: float = Field(default=0.33, description="Default k-factor for bend calculations")
    LOG_LEVEL: str = Field(default="INFO", description="Logging level")


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
