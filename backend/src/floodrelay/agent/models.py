"""The one place a model provider is chosen.

Every node asks for a *role* -- "heavy" for reasoning, "light" for extraction and
classification -- and this factory decides what that means. Nothing else in the
codebase constructs a model, so swapping provider is a single environment
variable rather than a search-and-replace across the graph.

That indirection is not architectural taste. Bedrock refuses Anthropic models by
connection origin from some countries, Nova has no such gate, and a laptop on a
bad link may need to fall back to a local Ollama model. All three are one
`MODEL_PROVIDER=` away.
"""

from __future__ import annotations

from typing import Literal, cast

from strands.models import Model

from ..config import Settings, get_settings

Role = Literal["heavy", "light"]


class ModelUnavailable(RuntimeError):
    """Raised when the configured provider cannot be constructed.

    Deliberately loud and specific: a console that silently falls back to a
    weaker model would give the coordinator triage decisions from a model they
    did not choose and cannot see.
    """


def model_id_for(role: Role, settings: Settings | None = None) -> str:
    s = settings or get_settings()
    if s.model_provider == "bedrock":
        return s.bedrock_model_heavy if role == "heavy" else s.bedrock_model_light
    if s.model_provider == "anthropic":
        return s.anthropic_model_heavy if role == "heavy" else s.anthropic_model_light
    return s.ollama_model_heavy if role == "heavy" else s.ollama_model_light


def get_model(role: Role, settings: Settings | None = None) -> Model:
    """Build the model for a role under the configured provider."""
    s = settings or get_settings()
    model_id = model_id_for(role, s)

    # Temperature is deliberately low everywhere: this is extraction and
    # triage, not writing. Creativity here shows up as invented casualties.
    temperature = 0.2 if role == "heavy" else 0.0

    try:
        if s.model_provider == "bedrock":
            from strands.models import BedrockModel

            return BedrockModel(
                model_id=model_id,
                region_name=s.aws_region,
                temperature=temperature,
            )

        if s.model_provider == "anthropic":
            from strands.models import AnthropicModel

            if not s.anthropic_api_key:
                raise ModelUnavailable("ANTHROPIC_API_KEY is not set.")
            return cast(
                Model,
                AnthropicModel(
                    client_args={"api_key": s.anthropic_api_key},
                    model_id=model_id,
                    max_tokens=4096,
                    params={"temperature": temperature},
                ),
            )

        from .ollama_compat import build_safe_ollama_model

        # A subclass, not the stock provider: the SDK's Ollama model crashes on
        # responses whose token counts are null. See agent/ollama_compat.py.
        ollama_model = build_safe_ollama_model()
        return cast(
            Model,
            ollama_model(
                host=s.ollama_host,
                model_id=model_id,
                temperature=temperature,
                # Cap generation. Extraction needs ~150 tokens and triage ~200;
                # without a ceiling a small model can ramble for thousands,
                # which at ~16 tok/s on CPU is minutes of wall clock.
                max_tokens=800,
                # Keep the weights resident. Reloading a 2-4 GB model between
                # requests costs more than the inference does.
                keep_alive="30m",
            ),
        )
    except ModelUnavailable:
        raise
    except ImportError as exc:  # optional provider extra not installed
        raise ModelUnavailable(
            f"Provider {s.model_provider!r} needs an optional dependency that is not "
            f"installed: {exc}."
        ) from exc
    except Exception as exc:
        raise ModelUnavailable(
            f"Could not build the {role} model ({model_id}) for provider "
            f"{s.model_provider!r}: {exc.__class__.__name__}: {exc}"
        ) from exc


def describe_models(settings: Settings | None = None) -> dict[str, str]:
    """What /healthz and the About screen report, without constructing anything."""
    s = settings or get_settings()
    return {
        "provider": s.model_provider,
        "heavy": model_id_for("heavy", s),
        "light": model_id_for("light", s),
    }
