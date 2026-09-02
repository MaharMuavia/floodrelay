"""Triage explanations.

The number is arithmetic (test_scoring.py). This file is about the sentence
that accompanies it: it has to read as plain prose in a dense console, and it
must never depend on the model being available or well-behaved.
"""

from __future__ import annotations

import pytest

from conftest import FIXED_NOW, make_need, make_request
from floodrelay.agent.nodes.triage import (
    _clean_explanation,
    deterministic_explanation,
    run,
)
from floodrelay.services.scoring import compute_urgency


@pytest.mark.parametrize(
    ("raw", "expected_start"),
    [
        ("> An elderly man is unable to walk.", "An elderly man"),
        ("- Four people are on a roof.", "Four people"),
        ("* Water is rising fast.", "Water is"),
        ("1. Three children are trapped.", "Three children"),
        ("**Four people** on a roof.", "Four people"),
    ],
)
def test_markdown_copied_from_the_prompt_is_stripped(raw: str, expected_start: str) -> None:
    """The prompt shows its example as a blockquote; small models copy the '>'."""
    assert _clean_explanation(raw).startswith(expected_start)


def test_no_markdown_markers_survive() -> None:
    cleaned = _clean_explanation("> **Three children** on a `roof` at Mohib Banda.")
    for marker in (">", "**", "`"):
        assert marker not in cleaned


def test_whitespace_and_newlines_are_collapsed() -> None:
    assert _clean_explanation("Four people\n  on a   roof.\n\n") == "Four people on a roof."


def test_ordinary_prose_is_left_alone() -> None:
    text = "Four people including three children are on a roof at Mohib Banda."
    assert _clean_explanation(text) == text


@pytest.mark.parametrize("text", ["", "   ", "\n\n"])
def test_empty_input_is_safe(text: str) -> None:
    assert _clean_explanation(text) == ""


# --- the deterministic fallback ---------------------------------------------


def test_the_explanation_never_needs_the_model() -> None:
    """If the model is down the coordinator still gets a usable sentence."""
    request = make_request(
        "r_1",
        need=make_need(kind="rescue", people_total=4, children=3,
                       water_level_note="on the roof"),
        received_at=FIXED_NOW,
    )
    urgency, breakdown, explanation = run(request, use_model=False)

    assert urgency == breakdown.total
    assert "4 people" in explanation and "3 children" in explanation
    assert explanation.endswith(".")
    assert ">" not in explanation


def test_the_fallback_names_the_biggest_driver() -> None:
    request = make_request("r_1", need=make_need(kind="rescue"), received_at=FIXED_NOW)
    breakdown = compute_urgency(request.need, received_at=FIXED_NOW, now=FIXED_NOW)
    text = deterministic_explanation(request, breakdown)
    assert "driven mostly by" in text


def test_an_unlocated_request_says_so() -> None:
    request = make_request("r_1", need=make_need(kind="rescue"), location=None)
    _urgency, _breakdown, explanation = run(request, use_model=False)
    assert "no confirmed location" in explanation.lower()


def test_the_urgency_returned_matches_the_breakdown_exactly() -> None:
    """The sentence may vary between runs; the number must not."""
    request = make_request("r_1", need=make_need(kind="medical", elderly=1))
    first = run(request, use_model=False)[0]
    second = run(request, use_model=False)[0]
    assert first == second


# --- the explanation must not invent evidence -------------------------------
#
# Observed on a live run: for a text-only message the model wrote
# "with recent photographs showing them on the ground". No photo existed. The
# urgency number was unaffected -- it is arithmetic -- but that sentence is what
# a coordinator reads before committing the only boat.

from floodrelay.agent.nodes.triage import _is_grounded  # noqa: E402


@pytest.mark.parametrize(
    "claim",
    [
        "Recent photographs show water at chest height.",
        "The photo shows people on a roof.",
        "Video footage confirms the street is flooded.",
        "The image suggests deep water.",
    ],
)
def test_citing_a_photo_that_does_not_exist_is_rejected(claim: str) -> None:
    request = make_request("r_1", need=make_need(kind="rescue"), photo_key=None)
    assert not _is_grounded(claim, request, None)


def test_citing_a_photo_is_fine_when_one_was_attached() -> None:
    request = make_request("r_1", need=make_need(kind="rescue"), photo_key="flood.jpg")
    assert _is_grounded("The photo shows water at chest height.", request, None)


def test_citing_rainfall_without_a_weather_reading_is_rejected() -> None:
    request = make_request("r_1", need=make_need(kind="rescue"))
    assert not _is_grounded("More rain is expected tonight.", request, None)


def test_citing_rainfall_is_fine_when_a_reading_was_supplied() -> None:
    request = make_request("r_1", need=make_need(kind="rescue"))
    assert _is_grounded("More rain is expected tonight.", request, "48mm in the last two days")


def test_an_ordinary_explanation_is_grounded() -> None:
    request = make_request("r_1", need=make_need(kind="rescue"))
    text = "Four people including three children are on a roof at Mohib Banda."
    assert _is_grounded(text, request, None)


def test_an_ungrounded_model_answer_falls_back_to_the_deterministic_sentence() -> None:
    """The coordinator gets a plain true sentence rather than an invented one."""
    import floodrelay.agent.nodes.triage as triage_mod

    request = make_request(
        "r_1",
        need=make_need(kind="rescue", people_total=4, children=3),
        received_at=FIXED_NOW,
        photo_key=None,
    )
    original = triage_mod.complete
    triage_mod.complete = lambda *a, **k: "Recent photographs show them stranded on a roof."
    try:
        _urgency, _breakdown, explanation = triage_mod.run(request, use_model=True)
    finally:
        triage_mod.complete = original

    assert "photograph" not in explanation.casefold()
    assert "4 people" in explanation
