"""The gate node: where the agent stops and asks.

Pure Python. No model. The four rules from the brief are hard-coded here because
a rule that a model can be talked out of is not a rule:

  1. `rescue` or `medical` -- always, whatever the confidence.
  2. Location confidence below the floor, after the one retry.
  3. Two or more open requests matched to the same resource.
  4. A possible duplicate scoring between the ask-floor and the auto-threshold.

The node writes a `DecisionCard` and halts the run. The graph does not resume
until a coordinator answers, at which point the pipeline re-enters at `match`
with their answer in state.

Card-writing style is part of the safety design, not decoration. The heading is
a sentence, both options state the same facts in the same order so the eye can
compare them vertically, buttons say what will happen, and there is always an
option that dispatches nobody.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from ...config import get_settings
from ...models.decision import DecisionCard, DecisionKind, DecisionOption
from ...models.request import HelpRequest
from ..nodes.dedupe import DuplicateVerdict
from ..nodes.match import MatchResult


def new_decision_id() -> str:
    return f"d_{uuid.uuid4().hex[:8]}"


@dataclass(frozen=True)
class GateOutcome:
    card: DecisionCard | None
    reason: str

    @property
    def halts(self) -> bool:
        return self.card is not None


def _facts_for(request: HelpRequest, match: MatchResult | None) -> dict[str, str]:
    """The same keys in the same order for every option, so they compare vertically."""
    need = request.need
    who = "not stated"
    if need is not None:
        bits: list[str] = []
        if need.people_total:
            bits.append(f"{need.people_total} people")
        if need.children:
            bits.append(f"{need.children} children")
        if need.elderly:
            bits.append(f"{need.elderly} elderly")
        if need.disabled:
            bits.append("1 who cannot move unaided")
        if need.pregnant:
            bits.append("1 pregnant")
        who = ", ".join(bits) if bits else "headcount not stated"

    water = (need.water_level_note if need and need.water_level_note else "not stated")
    distance = (
        f"{match.distance_m / 1000:.1f} km, {match.eta_min:.0f} min"
        if match and match.distance_m is not None and match.eta_min is not None
        else "unknown"
    )
    return {
        "Who": who,
        "Water": water,
        "Distance": distance,
        "Urgency": f"{request.urgency:.2f}" if request.urgency is not None else "not scored",
    }


def life_safety_card(request: HelpRequest, match: MatchResult | None) -> DecisionCard:
    kind_word = "rescue" if (request.need and request.need.kind == "rescue") else "medical"
    resource = match.resource if match and match.resource else None

    options = [
        DecisionOption(
            id="SEND",
            label=f"Send {resource.name}" if resource else "Approve and send the nearest team",
            request_id=request.id,
            resource_id=resource.id if resource else None,
            is_dispatch=True,
            facts=_facts_for(request, match),
        ),
        DecisionOption(
            id="HOLD",
            label="Hold for now",
            request_id=request.id,
            is_dispatch=False,
            facts={"Effect": "Nobody is sent. The request stays at the top of the queue."},
        ),
    ]
    reasoning = (
        match.reason if match and match.matched
        else "No suitable resource is free right now, so this needs your judgement."
    )
    return DecisionCard(
        id=new_decision_id(),
        kind="life_safety",
        request_ids=[request.id],
        heading=f"A {kind_word} call needs your approval.",
        recommendation_option_id="SEND" if resource else None,
        reasoning=reasoning,
        options=options,
        trace_id=request.trace_id,
    )


def low_confidence_location_card(request: HelpRequest) -> DecisionCard:
    said = (request.need.raw_location_text if request.need else None) or "nothing usable"

    options: list[DecisionOption] = []
    # Only offer the geocoder's guess when there actually is one. "Use nowhere"
    # is not a choice anyone can act on.
    if request.location is not None:
        options.append(
            DecisionOption(
                id="ACCEPT",
                label=f"Use {request.location.label}",
                request_id=request.id,
                is_dispatch=False,
                facts={
                    "Confidence": f"{request.location.confidence.score:.2f}",
                    "Source": "geocoder best guess",
                },
            )
        )
    options.append(
        DecisionOption(
            id="PICK",
            label="Pick a point on the map",
            request_id=request.id,
            is_dispatch=False,
            facts={"Effect": "You set the location, then matching runs again."},
        )
    )
    options.append(
        DecisionOption(
            id="ASK",
            label="Ask the caller for a landmark",
            request_id=request.id,
            is_dispatch=False,
            facts={"Effect": "Nothing is sent. The request waits for more detail."},
        )
    )

    return DecisionCard(
        id=new_decision_id(),
        kind="low_confidence_location",
        request_ids=[request.id],
        heading=f"Couldn't place {said!r}.",
        recommendation_option_id=None,
        reasoning=(
            request.location.confidence.reason
            if request.location
            else "The message did not give a location the geocoder recognises."
        ),
        options=options,
        trace_id=request.trace_id,
    )


def resource_conflict_card(
    request: HelpRequest, rival: HelpRequest, match: MatchResult, rival_match: MatchResult | None
) -> DecisionCard:
    resource = match.resource
    assert resource is not None  # only called when a resource was matched

    a, b = (request, rival) if (request.urgency or 0) >= (rival.urgency or 0) else (rival, request)
    a_match, b_match = (
        (match, rival_match) if a.id == request.id else (rival_match, match)
    )

    return DecisionCard(
        id=new_decision_id(),
        kind="resource_conflict",
        request_ids=[a.id, b.id],
        heading=f"One {resource.kind.replace('_', ' ')}, two calls.",
        recommendation_option_id="A",
        reasoning=(
            f"The agent suggests {a.id}: higher urgency at {a.urgency or 0:.2f} "
            f"against {b.urgency or 0:.2f}. {b.id} is reachable afterwards."
        ),
        options=[
            DecisionOption(
                id="A",
                label=f"Send {resource.name} to {a.id}",
                request_id=a.id,
                resource_id=resource.id,
                is_dispatch=True,
                facts=_facts_for(a, a_match),
            ),
            DecisionOption(
                id="B",
                label=f"Send {resource.name} to {b.id}",
                request_id=b.id,
                resource_id=resource.id,
                is_dispatch=True,
                facts=_facts_for(b, b_match),
            ),
            DecisionOption(
                id="HOLD",
                label="Neither - hold",
                is_dispatch=False,
                facts={"Effect": "Nobody is sent. Both stay in the queue."},
            ),
        ],
        trace_id=request.trace_id,
    )


def possible_duplicate_card(request: HelpRequest, verdict: DuplicateVerdict) -> DecisionCard:
    return DecisionCard(
        id=new_decision_id(),
        kind="possible_duplicate",
        request_ids=[request.id] + ([verdict.candidate_id] if verdict.candidate_id else []),
        heading="This may be the same household as an earlier call.",
        recommendation_option_id=None,
        reasoning=f"Similarity {verdict.score:.2f}. {verdict.reason}.",
        options=[
            DecisionOption(
                id="MERGE",
                label=f"Same household as {verdict.candidate_id}",
                request_id=request.id,
                is_dispatch=False,
                facts={"Effect": "This request is closed as a duplicate."},
            ),
            DecisionOption(
                id="SEPARATE",
                label="Different households",
                request_id=request.id,
                is_dispatch=False,
                facts={"Effect": "Both stay open and are matched separately."},
            ),
        ],
        trace_id=request.trace_id,
    )


def evaluate(
    request: HelpRequest,
    *,
    match: MatchResult | None = None,
    duplicate: DuplicateVerdict | None = None,
    rival: HelpRequest | None = None,
    rival_match: MatchResult | None = None,
    already_approved: bool = False,
) -> GateOutcome:
    """Apply the four rules in order. Returns a card, or a clear reason not to.

    Rule order matters: contention is checked before life-safety, because when
    two rescues want one boat the useful question is "which one", not "may I
    send someone".
    """
    settings = get_settings()

    # Rule 4 -- a possible duplicate, before anything is dispatched on it.
    if duplicate is not None and duplicate.needs_human:
        return GateOutcome(
            possible_duplicate_card(request, duplicate),
            f"possible duplicate at {duplicate.score:.2f}",
        )

    # Rule 2 -- we do not know where this is.
    unplaced = (
        request.location is None
        or request.location.confidence.score < settings.geo_confidence_floor
    )
    if unplaced and (request.location is None or request.geo_attempts >= 2):
        return GateOutcome(
            low_confidence_location_card(request),
            "location confidence below the floor after the retry",
        )

    # Rule 3 -- two open requests want the same resource.
    if (
        match is not None
        and rival is not None
        and match.has_conflict
        and match.resource is not None
    ):
        return GateOutcome(
            resource_conflict_card(request, rival, match, rival_match),
            f"{match.resource.id} is wanted by {len(match.contends_with) + 1} open requests",
        )

    # Rule 1 -- life safety, always.
    life_safety = request.need is not None and request.need.kind in {"rescue", "medical"}
    if life_safety and not already_approved:
        assert request.need is not None
        return GateOutcome(
            life_safety_card(request, match),
            f"{request.need.kind} always needs a human decision",
        )

    return GateOutcome(None, "no gate rule applies; the agent may proceed alone")


def kinds_requiring_human() -> tuple[DecisionKind, ...]:
    return ("life_safety", "low_confidence_location", "resource_conflict", "possible_duplicate")


def processing_failed_card(request: HelpRequest, error: str) -> DecisionCard:
    """The run failed outright. The coordinator still has to be able to act.

    Without this the request was left `needs_decision` with no card behind it:
    amber in the queue, nothing to answer, and no way forward. That breaks the
    one rule the console's colour scheme depends on -- if amber is on screen,
    there is something to answer -- and it silently stranded real requests.

    The message itself is intact and readable, so the useful options are to try
    again or to take it out of the agent's hands.
    """
    return DecisionCard(
        id=new_decision_id(),
        kind="processing_failed",
        request_ids=[request.id],
        heading="Couldn't read this message automatically.",
        recommendation_option_id="RETRY",
        reasoning=(
            f"The agent stopped partway through: {error}. The message itself is "
            f"intact and shown in full on the request, so nothing has been lost."
        ),
        options=[
            DecisionOption(
                id="RETRY",
                label="Try again",
                request_id=request.id,
                is_dispatch=False,
                facts={"Effect": "Runs the whole pipeline again from the start."},
            ),
            DecisionOption(
                id="MANUAL",
                label="Handle this one myself",
                request_id=request.id,
                is_dispatch=False,
                facts={"Effect": "Closes the request here. Nothing is sent."},
            ),
        ],
        trace_id=request.trace_id,
    )
