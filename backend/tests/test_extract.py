"""Extraction and grounding.

These tests drive `normalise` with the raw objects a model actually returns --
including the exact malformed shapes observed from the local model during the
build: `0` for "not stated", `1` for `true`, stray extra keys, and an invented
pregnancy. The model is not called here; grounding is deterministic and must be
testable without one.
"""

from __future__ import annotations

import pytest

from floodrelay.agent.nodes._llm import extract_json_object
from floodrelay.agent.nodes.extract import normalise

ROOF_URDU = (
    "Kheshgi Payan, pani bohot tez barh raha hai. 6 log hain, "
    "ek aurat hamla se hai. Chhat par aa gaye hain. Madad karo"
)
DISABLED_URDU = (
    "Akora Khattak, Mohalla Sethian. 9 log ek chhoti chhat par hain, "
    "4 bacche aur ek maazoor bhai jo chal nahi sakta. Pani seene tak. Boat bhejo please."
)
DONATION = (
    "Our organisation has 200 food packets ready for distribution. "
    "Where should we send them? We can deliver within Nowshera district today."
)


# --- the observed model output, corrected -----------------------------------


def test_roman_urdu_roof_case_is_read_correctly() -> None:
    """The exact reply the local model gave, with 0s and a 1 for pregnant."""
    raw = {
        "kind": "rescue",
        "people_total": 6,
        "children": 0,
        "elderly": 0,
        "pregnant": 1,
        "water_level_note": "water is rising",
        "raw_location_text": "Kheshgi Payan",
        "additional_notes": "people are on a roof",  # stray key, must be ignored
    }
    need, notes = normalise(raw, ROOF_URDU)

    assert need.kind == "rescue"
    assert need.people_total == 6
    assert need.children is None, "0 must become 'not stated', not zero children"
    assert need.elderly is None
    assert need.pregnant is True, "'ek aurat hamla se hai' grounds pregnant"
    assert need.raw_location_text == "Kheshgi Payan"
    assert any("treated as not stated" in n for n in notes)


def test_an_invented_pregnancy_is_dropped() -> None:
    """The model returned pregnant=1 for a message that never mentions it."""
    raw = {"kind": "rescue", "people_total": 9, "children": 4, "elderly": 1, "pregnant": 1}
    need, notes = normalise(raw, DISABLED_URDU)

    assert need.pregnant is None
    assert any("ungrounded pregnant" in n for n in notes)
    assert need.people_total == 9
    assert need.children == 4


def test_a_missed_disability_is_recovered_from_the_message() -> None:
    """'ek maazoor bhai jo chal nahi sakta' means disabled, even if the model missed it."""
    raw = {"kind": "rescue", "people_total": 9, "children": 4, "disabled": None}
    need, notes = normalise(raw, DISABLED_URDU)

    assert need.disabled is True
    assert any("the model missed it" in n for n in notes)


def test_an_ungrounded_count_is_dropped() -> None:
    raw = {"kind": "rescue", "people_total": 47, "children": 12}
    need, notes = normalise(raw, ROOF_URDU)

    assert need.people_total is None
    assert need.children is None
    assert sum(1 for n in notes if "ungrounded" in n) == 2


def test_counts_written_as_roman_urdu_words_are_grounded() -> None:
    raw = {"kind": "rescue", "people_total": 2}
    need, _ = normalise(raw, "do log chhat par phanse hain")
    assert need.people_total == 2


# --- the kind backstops -----------------------------------------------------


def test_a_roof_message_is_forced_to_rescue() -> None:
    """Gate rule 1 depends on this. The model called it 'other'."""
    need, notes = normalise({"kind": "other", "people_total": 6}, ROOF_URDU)
    assert need.kind == "rescue"
    assert any("overrode kind" in n for n in notes)


def test_a_donation_offer_is_not_a_food_request() -> None:
    """The model called this food_water. It is someone offering help."""
    need, notes = normalise({"kind": "food_water"}, DONATION)
    assert need.kind == "other"
    assert any("offers help rather than requesting" in n for n in notes)


def test_an_unknown_kind_falls_back_to_other() -> None:
    need, notes = normalise({"kind": "emergency_rescue_urgent"}, "we need help with food")
    assert need.kind == "other"
    assert any("unknown kind" in n for n in notes)


def test_an_offer_that_also_describes_a_rescue_stays_rescue() -> None:
    """Safety bias: if both signals appear, do not downgrade away from rescue."""
    need, _ = normalise(
        {"kind": "other"},
        "I have a boat and can help, but my own family is trapped "
        "on the roof at Pabbi",
    )
    assert need.kind == "rescue"


# --- the no-location case ---------------------------------------------------


def test_a_message_with_no_location_yields_null_location_text() -> None:
    need, _ = normalise(
        {"kind": "other", "raw_location_text": None}, "water everywhere please help"
    )
    assert need.raw_location_text is None


@pytest.mark.parametrize("placeholder", ["null", "none", "N/A", "unknown", "-", "  "])
def test_placeholder_strings_become_none(placeholder: str) -> None:
    need, _ = normalise({"kind": "other", "raw_location_text": placeholder}, "help us")
    assert need.raw_location_text is None


# --- confidence -------------------------------------------------------------


def test_a_clean_extraction_scores_high() -> None:
    raw = {"kind": "rescue", "people_total": 6, "pregnant": True,
           "raw_location_text": "Kheshgi Payan"}
    need, _ = normalise(raw, ROOF_URDU)
    assert need.extraction_confidence.score >= 0.85
    assert need.extraction_confidence.reason


def test_corrections_lower_the_confidence_and_are_explained() -> None:
    raw = {"kind": "rescue", "people_total": 99, "children": 42, "pregnant": 1}
    need, _ = normalise(raw, "water everywhere please help")
    assert need.extraction_confidence.score < 0.85
    assert "ungrounded" in need.extraction_confidence.reason


# --- tolerant JSON parsing --------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        '{"kind": "rescue"}',
        'Here is the JSON:\n```json\n{"kind": "rescue"}\n```',
        '<think>the caller is on a roof</think>{"kind": "rescue"}',
        'Sure! {"kind": "rescue"} Hope that helps.',
        '{"kind": "rescue",}',  # trailing comma
    ],
)
def test_json_is_recovered_from_messy_model_replies(text: str) -> None:
    assert extract_json_object(text) == {"kind": "rescue"}


def test_nested_braces_are_balanced_correctly() -> None:
    got = extract_json_object('{"kind": "rescue", "meta": {"a": 1}}')
    assert got == {"kind": "rescue", "meta": {"a": 1}}


def test_a_string_containing_a_brace_does_not_confuse_the_parser() -> None:
    got = extract_json_object('{"note": "water } rising"}')
    assert got == {"note": "water } rising"}


@pytest.mark.parametrize("text", ["", "no json here", "{unclosed", None])
def test_unparseable_replies_return_none(text: str | None) -> None:
    assert extract_json_object(text) is None  # type: ignore[arg-type]
