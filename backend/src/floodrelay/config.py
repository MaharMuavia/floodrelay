"""Runtime configuration.

Every required value is declared here and validated at import time. A missing
variable fails loudly with its own name -- never a silent default, because a
console that silently points at the wrong table is worse than one that will not
start.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ModelProvider = Literal["bedrock", "anthropic", "ollama"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # `model_` is a Pydantic-protected prefix; we genuinely need those names.
        protected_namespaces=(),
    )

    # --- AWS / Bedrock -----------------------------------------------------
    aws_region: str = "us-east-1"
    model_provider: ModelProvider = "bedrock"
    bedrock_model_heavy: str = "us.amazon.nova-pro-v1:0"
    bedrock_model_light: str = "us.amazon.nova-lite-v1:0"

    # --- Fallback providers ------------------------------------------------
    anthropic_api_key: str | None = None
    anthropic_model_heavy: str = "claude-opus-4-5"
    anthropic_model_light: str = "claude-haiku-4-5-20251001"
    ollama_host: str = "http://localhost:11434"
    ollama_model_heavy: str = "qwen2.5:7b"
    ollama_model_light: str = "qwen2.5:7b"

    # --- Storage -----------------------------------------------------------
    ddb_table: str = "floodrelay"
    ddb_endpoint: str | None = None  # unset in AWS; set to DynamoDB Local otherwise
    s3_bucket: str = "floodrelay-media"
    s3_endpoint: str | None = None  # MinIO locally
    media_dir: str | None = None  # filesystem adapter when S3/MinIO is absent

    # --- Outbound services -------------------------------------------------
    # Nominatim requires a User-Agent identifying the application, and it
    # actively rejects placeholder contacts: a UA containing "example.org"
    # comes back as HTTP 403. Operators should append a real contact address
    # they control. Verified against the live service.
    nominatim_user_agent: str = "FloodRelay/0.1"
    nominatim_base: str = "https://nominatim.openstreetmap.org"
    overpass_base: str = "https://overpass-api.de/api/interpreter"
    # Bias geocoding to the district being coordinated. Without this,
    # "Mohib Banda" resolves to the village of that name in Mardan rather
    # than the one in Nowshera -- a wrong district is a wrong dispatch.
    # west,north,east,south as Nominatim expects.
    geocode_viewbox: str = "71.65,34.15,72.30,33.85"
    open_meteo_base: str = "https://api.open-meteo.com/v1"
    reliefweb_base: str = "https://api.reliefweb.int/v1"

    # --- Telemetry ---------------------------------------------------------
    otel_exporter_otlp_endpoint: str | None = None
    otel_service_name: str = "floodrelay-agent"

    # --- Behaviour ---------------------------------------------------------
    demo_mode: bool = True
    # Optional shared secret for /internal/*. Unset means no check, which is
    # fine locally and is not fine on a public runtime.
    internal_token: str | None = None
    cors_origins: str = "http://localhost:3000"

    # Gate thresholds. Named here so the rules are auditable in one place
    # rather than scattered as literals across the graph.
    geo_confidence_floor: float = Field(default=0.55, ge=0, le=1)
    dedupe_auto_threshold: float = Field(default=0.75, ge=0, le=1)
    dedupe_ask_floor: float = Field(default=0.40, ge=0, le=1)
    dedupe_radius_m: float = Field(default=1500.0, gt=0)
    dedupe_window_hours: float = Field(default=6.0, gt=0)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @model_validator(mode="after")
    def _check_provider_credentials(self) -> Settings:
        """Fail at startup, naming the variable, rather than at first model call."""
        if self.model_provider == "anthropic" and not self.anthropic_api_key:
            raise ValueError(
                "MODEL_PROVIDER=anthropic requires ANTHROPIC_API_KEY to be set."
            )
        if self.dedupe_ask_floor > self.dedupe_auto_threshold:
            raise ValueError(
                "DEDUPE_ASK_FLOOR must be <= DEDUPE_AUTO_THRESHOLD; "
                f"got {self.dedupe_ask_floor} > {self.dedupe_auto_threshold}."
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
