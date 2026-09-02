"""The triage node: the model explains, `scoring.py` computes.

The urgency number is arithmetic. What this node adds is a sentence a tired
human can read at 2am and immediately agree or disagree with -- and if the model
is unavailable or unhelpful, the sentence is generated deterministically from
the same breakdown. The number never depends on the model being up.
"""

from __future__ import annotations

import re

from ...models.request import HelpRequest
from ...services.scoring import UrgencyBreakdown, compute_urgency
from ._llm import complete, load_prompt

_KIND_PHRASE = {
    "rescue": "people needing to be taken out of the water",
    "medical": "a medical need",
    "food_water": "food or drinking water",
    "shelter": "somewhere to stay",
    "other": "something outside the usual categories",
}


_MARKDOWN_PREFIX = re.compile(r"^\s*(?:[>*\-+]|\d+[.)])\s+", flags=re.MULTILINE)


def _clean_explanation(text: str) -> str:
    """Strip markdown the model copied from the prompt's worked example.

    The example in prompts/triage.md is shown as a blockquote so it reads as a
    sample, and small models reproduce the "> " along with the style. The
    console renders plain sentences, so a stray quote marker just looks broken.
    """
    cleaned = _MARKDOWN_PREFIX.sub("", text or "")
    cleaned = cleaned.replace("**", "").replace("`", "")
    return " ".join(cleaned.split()).strip()


# Evidence the explanation may only mention when it actually exists. Observed:
# the local model wrote "with recent photographs showing them on the ground"
# for a text-only message with no photo attached. The urgency number was
# unaffected -- it is arithmetic -- but the sentence beside it is what a
# coordinator reads before committing the only boat, and inventing evidence
# there is worse than saying nothing.
_PHOTO_WORDS = ("photo", "photograph", "image", "picture", "footage", "video")
_WEATHER_WORDS = ("rainfall", "forecast", "more rain", "rain is expected")


def _is_grounded(text: str, request: HelpRequest, rainfall_note: str | None) -> bool:
    """Reject an explanation that cites evidence this request does not have."""
    lowered = text.casefold()
    cites_absent_photo = request.photo_key is None and any(
        w in lowered for w in _PHOTO_WORDS
    )
    cites_absent_weather = rainfall_note is None and any(
        w in lowered for w in _WEATHER_WORDS
    )
    return not (cites_absent_photo or cites_absent_weather)


def deterministic_explanation(request: HelpRequest, breakdown: UrgencyBreakdown) -> str:
    """A plain-language reading of the score, with no model involved."""
    need = request.need
    parts: list[str] = []

    if need is not None:
        parts.append(f"This is {_KIND_PHRASE.get(need.kind, need.kind)}")
        who: list[str] = []
        if need.people_total:
            who.append(f"{need.people_total} people")
        if need.children:
            who.append(f"{need.children} children")
        if need.elderly:
            who.append(f"{need.elderly} elderly")
        if need.disabled:
            who.append("someone who cannot move unaided")
        if need.pregnant:
            who.append("a pregnant woman")
        if who:
            parts.append("involving " + ", ".join(who))
        if need.water_level_note:
            parts.append(f"water reported as {need.water_level_note.rstrip('.')}")

    if request.location is not None:
        parts.append(f"located at {request.location.label}")
    else:
        parts.append("with no confirmed location")

    driver = max(
        (("the type of need", breakdown.kind),
         ("who is involved", breakdown.vulnerability),
         ("the photo", breakdown.photo),
         ("the water level", breakdown.water_level),
         ("how recent it is", breakdown.recency)),
        key=lambda pair: pair[1],
    )[0]

    return (
        ". ".join(p[0].upper() + p[1:] if i == 0 else p for i, p in enumerate(parts))
        + f". Urgency {breakdown.total:.2f}, driven mostly by {driver}."
    )


def run(
    request: HelpRequest, *, rainfall_note: str | None = None, use_model: bool = True
) -> tuple[float, UrgencyBreakdown, str]:
    """Score the request and explain the score.

    Returns (urgency, breakdown, explanation). The first two are deterministic;
    only the third can vary between runs.
    """
    breakdown = compute_urgency(
        request.need,
        raw_text=request.raw_text,
        photo_severity=request.photo_severity,
        received_at=request.received_at,
    )
    fallback = deterministic_explanation(request, breakdown)
    if rainfall_note:
        fallback = f"{fallback} {rainfall_note}"

    if not use_model:
        return breakdown.total, breakdown, fallback

    try:
        system = load_prompt("triage")
        user = (
            f"Message: {request.raw_text}\n"
            f"Computed urgency: {breakdown.total:.2f}\n"
            f"Components: kind={breakdown.kind:.2f}, people={breakdown.vulnerability:.2f}, "
            f"photo={breakdown.photo:.2f}, water={breakdown.water_level:.2f}, "
            f"recency={breakdown.recency:.2f}\n"
            f"{'Weather: ' + rainfall_note if rainfall_note else ''}\n\n"
            f"Explain this score in one or two sentences:"
        )
        text = _clean_explanation(complete(system, user, role="heavy"))
        # Guard against a model that ignores the brief and writes an essay, or
        # tries to argue with the number -- and against one that invents
        # evidence. A fabricated explanation is worse than a plain one, so an
        # ungrounded answer falls back to the deterministic sentence.
        if text and 20 <= len(text) <= 600 and _is_grounded(text, request, rainfall_note):
            return breakdown.total, breakdown, text
    except Exception:
        pass

    return breakdown.total, breakdown, fallback
