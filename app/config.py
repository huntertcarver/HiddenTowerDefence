from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Secret aliases support agent and deployed names."""

    model_config = SettingsConfigDict(case_sensitive=True, extra="ignore")

    environment: Literal["local", "test", "production"] = "local"
    port: int = Field(default=8080, ge=1, le=65535)
    data_dir: Path = Path("./data")
    database_backend: Literal["sqlite", "spanner"] = "sqlite"
    sqlite_path: Path | None = None
    spanner_project_id: str | None = None
    spanner_instance_id: str = "smp-prod-shared-spanner"
    spanner_database_id: str = "hiddentowerdefence"

    apify_api_token: SecretStr | None = Field(
        default=None, validation_alias="APIFY_API_TOKEN"
    )
    apify_actor_id: str = "gentle_cloud/hacker-news-scraper"
    apify_fallback_actor_id: str = "onescales/hacker-news-data"
    apify_interval_seconds: int = Field(default=120, ge=15)
    apify_batch_size: int = Field(default=10, ge=1, le=20)
    apify_comment_limit: int = Field(default=10, ge=0, le=50)
    source_title_limit: int = Field(default=500, ge=100, le=2000)
    source_text_limit: int = Field(default=12000, ge=1000, le=50000)
    source_comment_limit: int = Field(default=2000, ge=100, le=10000)
    heartbeat_interval_seconds: int = Field(default=15, ge=5)
    heartbeat_lease_seconds: int = Field(default=45, ge=15)
    source_processing_lease_seconds: int = Field(default=300, ge=30, le=3600)

    hiddenlayer_client_id: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("HiddenLayer_API_ClientID", "HIDDENLAYER_CLIENT_ID"),
    )
    hiddenlayer_client_secret: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("HiddenLayer_API_ClientSecret", "HIDDENLAYER_CLIENT_SECRET"),
    )
    hiddenlayer_base_url: str = "https://api.hiddenlayer.ai"
    hiddenlayer_fail_closed: bool = True
    hiddenlayer_requester_id: str = "hidden-tower-defence"

    nvidia_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "NVIDIA_nemotron-3-ultra-550b-a55b_API_KEY", "NVIDIA_API_KEY"
        ),
    )
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_model: str = "nvidia/nemotron-3-ultra-550b-a55b"
    operator_token: SecretStr | None = Field(default=None, validation_alias="OPERATOR_TOKEN")
    operator_session_seconds: int = Field(default=1800, ge=300, le=86400)
    operator_login_attempts: int = Field(default=5, ge=1, le=20)
    operator_rate_window_seconds: int = Field(default=60, ge=10, le=3600)
    demo_interval_seconds: int = Field(default=8, ge=2, le=120)

    @field_validator("data_dir")
    @classmethod
    def expand_data_dir(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @property
    def resolved_sqlite_path(self) -> Path:
        return self.sqlite_path or self.data_dir / "hidden_tower.db"

    @property
    def requires_operator_token(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
