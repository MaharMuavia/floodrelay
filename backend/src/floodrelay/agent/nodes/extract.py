"""The extract node: unstructured message in, `ExtractedNeed` out.

The model reads the language. It does not get the last word on the facts.

Every number and every boolean the model returns is checked back against the
message before it is accepted -- a count must actually appear in the text, and
`pregnant: true` must correspond to a word that means pregnant. Anything that
fails the check is dropped to `null` and the reason is recorded in the
extraction confidence.

This matters more than it looks. A hallucinated `pregnant` or an invented child
inflates the vulnerability term in the urgency score and can push a calm request
above a genuine rescue. Grounding is what lets a small, cheap, local model be
used here safely: it can be wrong, but it cannot be wrong *and* believed.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from ...models.common import Confidence
from ...models.request import ExtractedNeed, NeedKind
from ._llm import complete_json, load_prompt

VALID_KINDS: tuple[NeedKind, ...] = ("rescue", "medical", "food_water", "shelter", "other")

# Number words the seed messages actually use, English and Roman Urdu.
_NUMBER_WORDS: dict[int, tuple[str, ...]] = {
    1: ("one", "ek", "aik"),
    2: ("two", "do", "dou"),
    3: ("three", "teen", "tin"),
    4: ("four", "char", "chaar"),
    5: ("five", "panch", "paanch"),
    6: ("six", "che", "chhe", "cheh"),
    7: ("seven", "saat", "sat"),
    8: ("eight", "aath", "ath"),
    9: ("nine", "nau", "no"),
    10: ("ten", "das", "dus"),
    12: ("twelve", "barah"),
}

_PREGNANT_TERMS = ("pregnant", "hamla", "hamila", "expecting", "umeed se")
_DISABLED_TERMS = (
    "disabled", "maazoor", "mazoor", "apahij", "wheelchair", "handicap",
    "cannot walk", "can not walk", "chal nahi sakta", "chal nahi sakti",
    "paralys", "bedridden",
)

# Roof or trapped-by-water language. Gate rule 1 depends on `rescue` being
# recognised, so this is a safety-relevant backstop, not a nicety.
# Strong signals only: "boat" and "rescue" are deliberately absent, because they appear
# just as often in offers of help ("I have a boat") as in calls for it, and a
# false rescue is a real cost -- it fires gate rule 1 and takes a coordinator's
# attention away from someone actually on a roof.
_RESCUE_TERMS = (
    "roof", "chhat", "chat par", "trapped", "phanse", "phansi", "stranded",
    "doob", "drowning", "surrounded by water", "cannot leave", "nikal nahi",
)
_OFFER_TERMS = (
    "i have a", "we have", "available from", "can help", "can deliver",
    "volunteer", "ready for distribution", "how can our", "on behalf of",
    "register as", "i am an", "our organisation has",
)


def _fold(text: str) -> str:
    folded = unicodedata.normalize("NFKD", (text or "").casefold())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", folded)


def _count_is_grounded(value: int, text: str) -> bool:
    """True if the message actually contains this number, as digits or a word."""
    haystack = _fold(text)
    if re.search(rf"(?<!\d){value}(?!\d)", haystack):
        return True
    return any(re.search(rf"\b{w}\b", haystack) for w in _NUMBER_WORDS.get(value, ()))


def _mentions(text: str, terms: tuple[str, ...]) -> bool:
    haystack = _fold(text)
    return any(t in haystack for t in terms)


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _as_bool(value: Any) -> bool | None:
    """Small models emit 1/0 and "yes"/"no" as often as true/false."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().casefold()
        if v in {"true", "yes", "1", "haan"}:
            return True
        if v in {"false", "no", "0", "nahi", "null", "none", ""}:
            return False
    return None


def _clean_text(value: Any, limit: int = 240) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned or cleaned.casefold() in {"null", "none", "n/a", "unknown", "-"}:
        return None
    return cleaned[:limit]


def normalise(raw: dict[str, Any], message: str) -> tuple[ExtractedNeed, list[str]]:
    """Coerce and ground a raw model object into an `ExtractedNeed`.

    Returns the need plus the list of corrections applied, which becomes the
    confidence reason shown in the UI and written to the audit log.
    """
    notes: list[str] = []

    # --- kind -------------------------------------------------------------
    kind_raw = str(raw.get("kind", "") or "").strip().casefold().replace(" ", "_")
    kind: NeedKind = kind_raw if kind_raw in VALID_KINDS else "other"
    if kind_raw not in VALID_KINDS:
        notes.append(f"model returned unknown kind {raw.get('kind')!r}, defaulted to other")

    # Backstop for gate rule 1: roof or trapped language means rescue, whatever
    # the model called it. This check runs before the offer check and wins over
    # it -- someone who offers a boat and then says their own family is on the
    # roof is a rescue, and the safe failure here is a false rescue, not a
    # missed one.
    describes_rescue = _mentions(message, _RESCUE_TERMS)
    if kind != "rescue" and describes_rescue:
        notes.append(
            f"overrode kind {kind} to rescue: message describes people trapped or on a roof"
        )
        kind = "rescue"

    # An offer of help is not a request for help -- unless it also describes one.
    if kind != "other" and not describes_rescue and _mentions(message, _OFFER_TERMS):
        notes.append(
            f"overrode kind {kind} to other: message offers help rather than requesting it"
        )
        kind = "other"

    # --- counts, each grounded in the message ------------------------------
    counts: dict[str, int | None] = {}
    for field in ("people_total", "children", "elderly"):
        value = _as_int(raw.get(field))
        if value is None:
            counts[field] = None
            continue
        if value == 0:
            # Models return 0 for "not mentioned" far more often than they mean it.
            counts[field] = None
            notes.append(f"{field}=0 treated as not stated")
            continue
        if not _count_is_grounded(value, message):
            counts[field] = None
            notes.append(f"dropped ungrounded {field}={value}: not found in the message")
            continue
        counts[field] = value

    # --- booleans, each requiring a matching word --------------------------
    pregnant = _as_bool(raw.get("pregnant"))
    if pregnant and not _mentions(message, _PREGNANT_TERMS):
        notes.append("dropped ungrounded pregnant=true: no word meaning pregnant in the message")
        pregnant = None
    elif pregnant is False:
        pregnant = None  # "not mentioned", not "confirmed not pregnant"

    disabled = _as_bool(raw.get("disabled"))
    if disabled and not _mentions(message, _DISABLED_TERMS):
        notes.append("dropped ungrounded disabled=true: no word meaning disabled in the message")
        disabled = None
    elif disabled is False:
        disabled = None

    if disabled is None and _mentions(message, _DISABLED_TERMS):
        disabled = True
        notes.append("set disabled=true from the message; the model missed it")
    if pregnant is None and _mentions(message, _PREGNANT_TERMS):
        pregnant = True
        notes.append("set pregnant=true from the message; the model missed it")

    # --- confidence --------------------------------------------------------
    score = 0.9
    score -= 0.1 * sum(1 for n in notes if n.startswith("dropped"))
    score -= 0.15 if any(n.startswith("model returned unknown kind") for n in notes) else 0.0
    score -= 0.05 * sum(1 for n in notes if n.startswith("overrode"))
    if not any(counts.values()):
        score -= 0.1
        notes.append("no headcount stated")
    score = max(0.1, min(1.0, score))

    reason = "; ".join(notes) if notes else "all fields read directly from the message"

    need = ExtractedNeed(
        kind=kind,
        people_total=counts["people_total"],
        children=counts["children"],
        elderly=counts["elderly"],
        disabled=disabled,
        pregnant=pregnant,
        water_level_note=_clean_text(raw.get("water_level_note")),
        raw_location_text=_clean_text(raw.get("raw_location_text")),
        contact_hint=_clean_text(raw.get("contact_hint")),
        extraction_confidence=Confidence(score=round(score, 3), reason=reason),
    )
    return need, notes


def run(message: str, *, extra_context: str | None = None) -> tuple[ExtractedNeed, str]:
    """Extract a need from one message. Returns the need and the raw model reply."""
    system = load_prompt("extract")
    user = f"Message: {message}\n\nJSON:"
    if extra_context:
        user = f"{user}\n\nAdditional context: {extra_context}"

    parsed, raw = complete_json(system, user, role="light")
    if parsed is None:
        # The pipeline continues with a low-confidence shell rather than
        # dropping the request: an unparsed message still needs a human.
        need = ExtractedNeed(
            kind="other",
            extraction_confidence=Confidence(
                score=0.1, reason="the model did not return readable JSON for this message"
            ),
        )
        return need, raw

    need, _notes = normalise(parsed, message)
    return need, raw
