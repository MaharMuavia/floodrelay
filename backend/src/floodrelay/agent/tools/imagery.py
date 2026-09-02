"""Photo severity scoring.

Designed for a vision model (Nova Pro on Bedrock, or Claude via the direct API):
given a photo of a flooded street or house, return 0..1 severity plus a one-line
justification, which feeds the 0.20 photo term in the urgency formula.

Honesty note, and it is deliberately loud: **the local Ollama models configured
in this build cannot see images.** `deepseek-r1:7b` advertises
`['completion','thinking']` and `phi3:mini` advertises `['completion']` --
neither has vision. Under MODEL_PROVIDER=ollama this function therefore returns
`available: false` and contributes *nothing* to the urgency score, rather than
inventing a number.

That is the difference between a stub and a lie: the score is absent, the UI
says so on the request detail screen, and the README says so too. Switching
MODEL_PROVIDER to bedrock or anthropic turns it on with no other change.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from strands import tool

from ...config import get_settings

# Providers whose configured model can actually look at a photograph.
VISION_CAPABLE_PROVIDERS = frozenset({"bedrock", "anthropic"})

PROMPT = (
    "You are looking at a photograph from a flood-affected area. "
    "Rate the visible severity from 0.0 to 1.0, where 0.0 is a wet road and "
    "1.0 is water above the ground floor of buildings or people stranded above "
    "the waterline. Judge only what you can see. "
    'Reply with JSON only: {"severity": <number>, "justification": "<one short sentence>"}'
)


def vision_available(provider: str | None = None) -> bool:
    return (provider or get_settings().model_provider) in VISION_CAPABLE_PROVIDERS


def _load_image(s3_key: str) -> tuple[bytes, str] | None:
    """Read the photo from the local media dir or the seed folder.

    S3/MinIO is the deployment path; locally we read from disk so the demo runs
    without object storage.
    """
    s = get_settings()
    roots = [Path(s.media_dir)] if s.media_dir else []
    roots.append(Path(__file__).resolve().parents[4] / "seed" / "photos")
    for root in roots:
        candidate = root / Path(s3_key).name
        if candidate.is_file():
            suffix = candidate.suffix.lower().lstrip(".")
            return candidate.read_bytes(), ("jpeg" if suffix in {"jpg", "jpeg"} else suffix)
    return None


def score(s3_key: str) -> dict[str, Any]:
    """Severity 0..1 for a photo, or an explicit unavailable result."""
    s = get_settings()
    if not vision_available(s.model_provider):
        return {
            "available": False,
            "severity": None,
            "reason": (
                f"The configured provider {s.model_provider!r} has no vision model, so no "
                f"photo severity was computed. Urgency for this request excludes the photo term."
            ),
        }

    loaded = _load_image(s3_key)
    if loaded is None:
        return {"available": False, "severity": None, "reason": f"photo {s3_key!r} not found"}

    image_bytes, image_format = loaded
    try:
        from strands import Agent

        from ..models import get_model
        from ..nodes._llm import extract_json_object

        agent = Agent(model=get_model("heavy"), callback_handler=None)
        content: list[Any] = [
            {"image": {"format": image_format, "source": {"bytes": image_bytes}}},
            {"text": PROMPT},
        ]
        parsed = extract_json_object(str(agent(content))) or {}

        raw_severity = parsed.get("severity")
        if not isinstance(raw_severity, int | float):
            return {
                "available": False,
                "severity": None,
                "reason": "the vision model did not return a numeric severity",
            }
        return {
            "available": True,
            "severity": max(0.0, min(1.0, float(raw_severity))),
            "justification": str(parsed.get("justification") or "").strip(),
        }
    except Exception as exc:
        # A vision failure must never take the pipeline down; the photo term
        # simply does not contribute.
        return {
            "available": False,
            "severity": None,
            "reason": f"vision scoring failed: {exc.__class__.__name__}: {exc}",
        }


@tool
def score_photo(s3_key: str) -> dict[str, Any]:
    """Rate flood severity visible in a photo, from 0.0 to 1.0.

    Args:
        s3_key: Key or filename of the stored photo.

    Returns:
        A dict with `severity` (0..1) and a one-line `justification`. If the
        configured model cannot see images, returns `available: false` with a
        `reason` and no severity -- it never guesses a number.
    """
    return score(s3_key)


def encode_data_uri(path: Path) -> str:
    """Helper for the request-detail screen to show the photo inline."""
    suffix = path.suffix.lower().lstrip(".") or "jpeg"
    mime = "image/jpeg" if suffix in {"jpg", "jpeg"} else f"image/{suffix}"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"
