"""Deterministic urgency scoring.

The model explains; this module computes. Urgency is never an LLM output --
that is the single design decision that makes the rest of the console
trustworthy, so this file is pure, dependency-free and table-tested.

    urgency = 0.40 * kind_weight
            + 0.25 * vulnerability
            + 0.20 * photo_severity
            + 0.10 * water_level_signal
            + 0.05 * recency

Every component is normalised to 0..1 before weighting, so the total is 0..1.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ..models.request import ExtractedNeed, NeedKind

WEIGHTS: dict[str, float] = {
    "kind": 0.40,
    "vulnerability": 0.25,
    "photo": 0.20,
    "water_level": 0.10,
    "recency": 0.05,
}

KIND_WEIGHT: dict[NeedKind, float] = {
    "rescue": 1.0,
    "medical": 0.9,
    "shelter": 0.6,
    "food_water": 0.4,
    "other": 0.2,
}

# Recency decays to zero over this window; a six-hour-old call is not urgent
# in the same way a six-minute-old one is.
RECENCY_HALFLIFE = timedelta(hours=6)

# Water-level phrases, English and Roman Urdu, strongest first. Matching is
# substring-based on a lowercased message; the first hit wins.
_WATER_SIGNALS: list[tuple[float, tuple[str, ...]]] = [
    (1.00, ("roof", "chhat", "chat par", "second floor", "upar chad", "neck deep", "gale tak")),
    (0.85, ("chest", "seene tak", "chaati", "shoulder", "kandhe")),
    (0.65, ("waist", "kamar tak", "kamar", "thigh", "ran tak")),
    (0.45, ("knee", "ghutne", "ghutno")),
    (0.25, ("ankle", "takhne", "paon tak", "shin")),
    (0.55, ("rising fast", "tezi se", "barh raha", "badh raha", "rising")),
]


@dataclass(frozen=True)
class UrgencyBreakdown:
    """Per-component contributions, so the UI can show the maths on hover."""

    kind: float
    vulnerability: float
    photo: float
    water_level: float
    recency: float
    total: float

    def as_dict(self) -> dict[str, float]:
        return {
            "kind": self.kind,
            "vulnerability": self.vulnerability,
            "photo": self.photo,
            "water_level": self.water_level,
            "recency": self.recency,
            "total": self.total,
        }


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def vulnerability_score(need: ExtractedNeed) -> float:
    """Vulnerability, capped at 1.0.

    Unstated (`None`) contributes nothing -- it is not evidence of absence.
    Headcount saturates rather than scaling linearly: the difference between
    one child and three matters far more than between eleven and thirteen.
    """
    score = 0.0
    if need.children:
        score += min(0.40, 0.20 * need.children)
    if need.elderly:
        score += min(0.30, 0.15 * need.elderly)
    if need.disabled:
        score += 0.25
    if need.pregnant:
        score += 0.25
    if need.people_total and need.people_total >= 5:
        score += 0.15
    return _clamp(score)


def water_level_signal(text: str | None) -> float:
    """Keyword signal for how deep the water is, from the raw message."""
    if not text:
        return 0.0
    haystack = re.sub(r"\s+", " ", text.lower())
    best = 0.0
    for weight, phrases in _WATER_SIGNALS:
        if any(p in haystack for p in phrases):
            best = max(best, weight)
    return best


def recency_score(received_at: datetime, now: datetime | None = None) -> float:
    """1.0 at arrival, decaying linearly to 0.0 at RECENCY_HALFLIFE."""
    now = now or datetime.now(UTC)
    if received_at.tzinfo is None:
        received_at = received_at.replace(tzinfo=UTC)
    age = (now - received_at).total_seconds()
    if age <= 0:
        return 1.0
    return _clamp(1.0 - age / RECENCY_HALFLIFE.total_seconds())


def compute_urgency(
    need: ExtractedNeed | None,
    *,
    raw_text: str | None = None,
    photo_severity: float | None = None,
    received_at: datetime | None = None,
    now: datetime | None = None,
) -> UrgencyBreakdown:
    """Total urgency plus its per-component breakdown.

    A request with nothing extracted yet still scores -- on recency alone --
    so that unprocessed items sort sensibly instead of sitting at the bottom.
    """
    kind_raw = KIND_WEIGHT[need.kind] if need is not None else 0.0
    vuln_raw = vulnerability_score(need) if need is not None else 0.0
    photo_raw = _clamp(photo_severity) if photo_severity is not None else 0.0

    # Prefer the extractor's dedicated note, fall back to the whole message.
    water_text = (need.water_level_note if need else None) or raw_text
    water_raw = water_level_signal(water_text)

    recency_raw = recency_score(received_at, now) if received_at is not None else 1.0

    kind = WEIGHTS["kind"] * kind_raw
    vulnerability = WEIGHTS["vulnerability"] * vuln_raw
    photo = WEIGHTS["photo"] * photo_raw
    water = WEIGHTS["water_level"] * water_raw
    recency = WEIGHTS["recency"] * recency_raw

    total = _clamp(round(kind + vulnerability + photo + water + recency, 6))
    return UrgencyBreakdown(
        kind=kind,
        vulnerability=vulnerability,
        photo=photo,
        water_level=water,
        recency=recency,
        total=total,
    )
