"""The triage node: the model explains, `scoring.py` computes.

The urgency number is arithmetic. What this node adds is a sentence a tired
human can read at 2am and immediately agree or disagree with -- and if the model
is unavailable or unhelpful, the sentence is generated deterministically from
the same breakdown. The number never depends on the model being up.
"""

from __future__ import annotations

import re
from typing import Any

from ...models.request import HelpRequest
from ...services.scoring import UrgencyBreakdown, compute_urgency
from ..tool_agent import run_agent
from ._llm import complete, load_prompt, tools_are_live

# Appended to prompts/triage.md only when the provider can tool-call. Kept out of
# the prompt file so the text a reviewer reads is the one a completion-only model
# is given, with no instructions about tools it does not have.
_TOOL_GUIDANCE = """
## Looking things up

You may call the tools available to you before you write the sentence: rainfall
and river discharge at the request's coordinates, the national situation report,
global flood alerts, and what is on the resource roster.

- Call at most two. A coordinator is waiting.
- Mention a reading **only if a tool actually returned it.** If a tool reports
  `available: false`, say nothing about that subject at all.
- The urgency score is still not yours to change. Nothing a tool returns can
  raise or lower it; it was computed before you were asked.
"""

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
# Tools whose output legitimately entitles an explanation to talk about weather.
_WEATHER_TOOLS = ("rainfall", "river_discharge")


def _is_grounded(
    text: str,
    request: HelpRequest,
    rainfall_note: str | None,
    tools_called: tuple[str, ...] = (),
) -> bool:
    """Reject an explanation that cites evidence this request does not have.

    `tools_called` is the list of tools the model actually invoked on this turn.
    A model that called `rainfall` and then wrote about the forecast has earned
    the right to; a model that wrote about it having called nothing has not. The
    check is against what ran, never against what the model claims ran.
    """
    lowered = text.casefold()
    cites_absent_photo = request.photo_key is None and any(
        w in lowered for w in _PHOTO_WORDS
    )
    fetched_weather = any(t in tools_called for t in _WEATHER_TOOLS)
    cites_absent_weather = (
        rainfall_note is None
        and not fetched_weather
        and any(w in lowered for w in _WEATHER_WORDS)
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


def _context_tools() -> list[Any]:
    """The tools the explanation agent may reach for, and nothing more.

    Every one of them is read-only. `roster_search` is here so the model can say
    "the nearest boat is twenty minutes away"; `roster_assign` deliberately is
    not, because an explanation has no business dispatching anyone and the
    cheapest way to guarantee that is to never put the tool in the list. The
    human gate would refuse it anyway -- this is the second lock, not the first.
    """
    from ..tools.gdacs import global_flood_alerts
    from ..tools.ndma import situation as ndma_situation
    from ..tools.river import river_discharge
    from ..tools.roster import roster_search
    from ..tools.weather import rainfall

    return [rainfall, river_discharge, ndma_situation, global_flood_alerts, roster_search]


def run(
    request: HelpRequest,
    *,
    rainfall_note: str | None = None,
    use_model: bool = True,
    request_id: str | None = None,
    trace_id: str | None = None,
) -> tuple[float, UrgencyBreakdown, str]:
    """Score the request and explain the score.

    Returns (urgency, breakdown, explanation). The first two are deterministic;
    only the third can vary between runs.

    With a tool-calling provider the model may look up rainfall, river discharge,
    the national situation report, global alerts, or what is on the roster before
    it writes the sentence. None of that reaches the number: `compute_urgency` is
    called first, from the message alone, and the result is already fixed before
    the model is asked anything. The tools change what the coordinator is told,
    never where the request sits in the queue.
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

        tools_called: tuple[str, ...] = ()
        if tools_are_live():
            location = request.location
            where = (
                f"The request is at {location.lat:.4f}, {location.lon:.4f} "
                f"({location.label})."
                if location is not None
                else "This request has no confirmed location, so point tools will not help."
            )
            raw, trace = run_agent(
                role="heavy",
                tools=_context_tools(),
                system_prompt=f"{system}\n{_TOOL_GUIDANCE}",
                user=f"{where}\n{user}",
                request_id=request_id or request.id,
                trace_id=trace_id or request.trace_id,
                node="triage",
            )
            tools_called = tuple(trace.names)
        else:
            raw = complete(system, user, role="heavy")

        text = _clean_explanation(raw)
        # Guard against a model that ignores the brief and writes an essay, or
        # tries to argue with the number -- and against one that invents
        # evidence. A fabricated explanation is worse than a plain one, so an
        # ungrounded answer falls back to the deterministic sentence.
        if (
            text
            and 20 <= len(text) <= 600
            and _is_grounded(text, request, rainfall_note, tools_called)
        ):
            return breakdown.total, breakdown, text
    except Exception:
        pass

    return breakdown.total, breakdown, fallback
