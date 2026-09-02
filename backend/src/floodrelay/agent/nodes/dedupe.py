"""The dedupe node: is this the same household we already heard from?

Two reports of one family are not two families, and a console that thinks
otherwise sends two boats to one roof while someone else waits.

Similarity is computed deterministically -- distance, time, need kind, headcount
agreement and word overlap -- rather than asked of the model. The reason is the
same one that keeps urgency out of the LLM: a duplicate judgement decides
whether a real request quietly disappears, and that decision has to be auditable
and reproducible. The model's role here is to *explain* the verdict in the card,
not to reach it.

Thresholds come from config so the gate rules and this node cannot drift apart:

  >= 0.75  auto-merge, the agent proceeds alone
  0.40 to 0.75  raise a `possible_duplicate` card for a human
  <  0.40  treat as distinct
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta

from ...config import get_settings
from ...models.request import HelpRequest
from ...services.geo import haversine_m

_WORD = re.compile(r"[a-z0-9]+")
_STOPWORDS = {
    "the", "and", "for", "are", "hai", "hain", "please", "help", "madad", "ko",
    "ke", "ka", "ki", "mein", "par", "hum", "we", "our", "is", "at", "in", "to",
    "need", "us", "there", "have", "with",
}


@dataclass(frozen=True)
class DuplicateVerdict:
    candidate_id: str | None
    score: float
    reason: str

    @property
    def is_duplicate(self) -> bool:
        return self.candidate_id is not None and self.score >= get_settings().dedupe_auto_threshold

    @property
    def needs_human(self) -> bool:
        s = get_settings()
        return (
            self.candidate_id is not None
            and s.dedupe_ask_floor <= self.score < s.dedupe_auto_threshold
        )


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD.findall((text or "").casefold()) if w not in _STOPWORDS and len(w) > 2}


def _overlap(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def similarity(a: HelpRequest, b: HelpRequest) -> tuple[float, list[str]]:
    """Score how likely two requests are the same household, with reasons."""
    reasons: list[str] = []
    score = 0.0

    # Location.
    #
    # A geocoded village name resolves to one centroid, so two unrelated
    # households in Pir Sabaq land on identical coordinates. Treating that as
    # "same spot" merged two genuine rescue calls during an end-to-end run and
    # made one of them disappear. Coincident points that came from the same
    # place *name* are evidence of the same village, nothing more -- so the
    # weight moves onto headcount and wording, which actually distinguish a
    # repeat from a neighbour.
    if a.location and b.location:
        metres = haversine_m(a.location.lat, a.location.lon, b.location.lat, b.location.lon)
        if metres > get_settings().dedupe_radius_m:
            return 0.0, [f"{metres / 1000:.1f} km apart, too far to be the same household"]

        same_place_name = (
            a.location.source == "nominatim"
            and b.location.source == "nominatim"
            and a.location.label == b.location.label
        )
        if same_place_name:
            score += 0.15
            reasons.append(
                f"both geocoded to {a.location.label!r}, which locates the village "
                f"rather than the household"
            )
        elif metres <= 150:
            score += 0.45
            reasons.append(f"same spot, {metres:.0f} m apart")
        elif metres <= 600:
            score += 0.30
            reasons.append(f"{metres:.0f} m apart")
        else:
            score += 0.15
            reasons.append(f"{metres / 1000:.1f} km apart")
    else:
        reasons.append("one or both have no resolved location")

    # Same kind of need.
    if a.need and b.need:
        if a.need.kind == b.need.kind:
            score += 0.15
            reasons.append(f"both {a.need.kind}")
        else:
            score -= 0.10
            reasons.append(f"different needs ({a.need.kind} vs {b.need.kind})")

        counts_a = (a.need.people_total, a.need.children)
        counts_b = (b.need.people_total, b.need.children)
        if any(counts_a) and counts_a == counts_b:
            score += 0.25
            reasons.append("identical headcount")
        elif (
            a.need.people_total
            and b.need.people_total
            and a.need.people_total != b.need.people_total
        ):
            score -= 0.05
            reasons.append(
                f"headcounts differ ({a.need.people_total} vs {b.need.people_total})"
            )

    # Time proximity.
    gap = abs((a.received_at - b.received_at).total_seconds()) / 3600
    if gap <= 1:
        score += 0.15
        reasons.append(f"{gap * 60:.0f} minutes apart")
    elif gap <= get_settings().dedupe_window_hours:
        score += 0.08
        reasons.append(f"{gap:.1f} hours apart")

    # Wording.
    overlap = _overlap(a.raw_text, b.raw_text)
    if overlap >= 0.30:
        score += 0.20
        reasons.append(f"{overlap:.0%} of the wording matches")
    elif overlap >= 0.15:
        score += 0.10
        reasons.append(f"{overlap:.0%} of the wording matches")

    return max(0.0, min(1.0, round(score, 3))), reasons


def candidates(
    request: HelpRequest, others: list[HelpRequest]
) -> list[HelpRequest]:
    """Open requests close enough in time and space to be worth comparing."""
    s = get_settings()
    window = timedelta(hours=s.dedupe_window_hours)
    out: list[HelpRequest] = []
    for other in others:
        if other.id == request.id or other.status in {"duplicate", "closed"}:
            continue
        if abs(other.received_at - request.received_at) > window:
            continue
        if request.location and other.location:
            metres = haversine_m(
                request.location.lat, request.location.lon,
                other.location.lat, other.location.lon,
            )
            if metres > s.dedupe_radius_m:
                continue
        out.append(other)
    return out


def run(request: HelpRequest, others: list[HelpRequest]) -> DuplicateVerdict:
    """Compare against open requests nearby and recent."""
    pool = candidates(request, others)
    if not pool:
        return DuplicateVerdict(None, 0.0, "no other open request nearby in the last few hours")

    best_id, best_score, best_reasons = None, 0.0, ["nothing similar enough"]
    for other in pool:
        score, reasons = similarity(request, other)
        if score > best_score:
            best_id, best_score, best_reasons = other.id, score, reasons

    if best_id is None or best_score < get_settings().dedupe_ask_floor:
        return DuplicateVerdict(
            None,
            best_score,
            f"closest of {len(pool)} nearby requests scored {best_score:.2f}, below the threshold",
        )

    return DuplicateVerdict(best_id, best_score, "; ".join(best_reasons))
