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
    # Whether the configured Ollama models can actually tool-call. Off by
    # default because the small local models this was developed against
    # (phi3:mini, deepseek-r1:7b) advertise completion only, and claiming
    # tool-calling that does not work is worse than not claiming it. Models
    # that do support tools -- qwen2.5, llama3.1, mistral-nemo -- turn it on.
    ollama_tool_calling: bool = False

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
    # GloFAS river discharge. Separate host from the weather API, same provider.
    open_meteo_flood_base: str = "https://flood-api.open-meteo.com/v1"
    # NASA GIBS serves satellite tiles with no key and no registration. The
    # capabilities document is ~5.8 MB, so it is cached hard -- see
    # agent/tools/imagery_layers.py.
    gibs_base: str = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best"
    # NDMA publishes a daily monsoon situation report as a PDF. There is no API;
    # the listing is scraped and the PDF parsed. See agent/tools/ndma.py.
    ndma_base: str = "https://ndma.gov.pk"
    # GDACS: worldwide flood alerts, keyless RSS. The only genuinely global
    # live source here. See agent/tools/gdacs.py.
    gdacs_rss: str = "https://www.gdacs.org/xml/rss.xml"
    # The district and province this console coordinates, used when asking the
    # national sitrep about "here".
    situation_district: str = "Nowshera"
    situation_province: str = "KP"
    # The point satellite layers are probed at, to establish whether a layer
    # actually serves tiles over the district being coordinated.
    situation_lat: float = 34.0151
    situation_lon: float = 71.9747
    # v1 was decommissioned and answers 410 Gone. v2 additionally requires a
    # pre-approved appname (a manual form, reviewed by email), so with none
    # configured the tool uses the keyless RSS feed instead. See
    # agent/tools/reliefweb.py.
    reliefweb_base: str = "https://api.reliefweb.int/v2"
    reliefweb_rss: str = "https://reliefweb.int/updates/rss.xml"
    reliefweb_appname: str | None = None

    # --- Telemetry ---------------------------------------------------------
    otel_exporter_otlp_endpoint: str | None = None
    otel_service_name: str = "floodrelay-agent"

    # --- Behaviour ---------------------------------------------------------
    demo_mode: bool = True
    # Optional shared secret for /internal/*. Unset means no check, which is
    # fine locally and is not fine on a public runtime.
    internal_token: str | None = None
    # The app secret for POST /intake/webhook, used to verify the
    # X-Hub-Signature-256 HMAC that WhatsApp Business sends over the raw body.
    # Unlike INTERNAL_TOKEN this one fails *closed*: with no secret configured
    # the route refuses every request. /internal/rescan is reached by our own
    # scheduler; the webhook is a public URL anybody can post help requests to,
    # and an unauthenticated one is a queue anybody can fill.
    webhook_secret: str | None = None
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
