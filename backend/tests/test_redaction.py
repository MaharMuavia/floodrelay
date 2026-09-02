"""PII redaction.

Names and phone numbers must not reach a model, the store, or the audit log.
This runs at intake, so if it leaks, the leak is permanent and everywhere.

Two failure directions matter equally. Missing a name leaks a real person's
identity. Over-matching corrupts the message a coordinator has to read at 2am,
and "PERSON_1 help" is not something anyone can act on.
"""

from __future__ import annotations

import pytest

from floodrelay.services.redaction import Redactor


@pytest.fixture
def redactor() -> Redactor:
    return Redactor()


# --- phone numbers ----------------------------------------------------------


@pytest.mark.parametrize(
    "number",
    [
        "0300-5550101",
        "0300 5550101",
        "03005550101",
        "+92 300 5550101",
        "+923005550101",
    ],
)
def test_pakistani_mobile_formats_are_removed(redactor: Redactor, number: str) -> None:
    out = redactor.redact(f"We are on the roof. Call {number} please")
    assert number not in out
    assert "CALLER_1" in out


def test_the_same_number_maps_to_the_same_pseudonym(redactor: Redactor) -> None:
    """Dedupe relies on this: two reports from one phone are one household."""
    first = redactor.redact("on the roof 0300-5550101")
    second = redactor.redact("still waiting 0300-5550101")
    assert "CALLER_1" in first
    assert "CALLER_1" in second


def test_two_different_numbers_get_different_pseudonyms(redactor: Redactor) -> None:
    out = redactor.redact("call 0300-5550101 or 0321-5550202")
    assert "CALLER_1" in out
    assert "CALLER_2" in out


def test_an_email_is_removed(redactor: Redactor) -> None:
    out = redactor.redact("reach me at asif@example.org")
    assert "asif@example.org" not in out
    assert "EMAIL_1" in out


def test_a_cnic_is_removed(redactor: Redactor) -> None:
    out = redactor.redact("my id is 17301-1234567-1")
    assert "17301-1234567-1" not in out
    assert "ID_1" in out


# --- signatures -------------------------------------------------------------


def test_an_inline_signature_is_removed(redactor: Redactor) -> None:
    """Found in production data: people sign off mid-sentence, not on a new line."""
    out = redactor.redact(
        "Neighbour ki family chhat par phansi hui hai, Mohib Banda. "
        "Koi boat bhejo jaldi. - Asif"
    )
    assert "Asif" not in out
    assert "PERSON_1" in out


def test_a_signature_on_its_own_line_is_removed(redactor: Redactor) -> None:
    out = redactor.redact("we are on the roof\n- Fatima Bibi")
    assert "Fatima" not in out


def test_a_two_word_signature_is_removed_whole(redactor: Redactor) -> None:
    out = redactor.redact("Send boat to Pabbi - Asif Khan")
    assert "Asif" not in out and "Khan" not in out


@pytest.mark.parametrize("dash", ["-", "–", "—"])  # noqa: RUF001
def test_every_dash_style_is_recognised(redactor: Redactor, dash: str) -> None:
    assert "Asif" not in redactor.redact(f"send help {dash} Asif")


# --- the opposite error: not over-matching ----------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Water rising fast - Please help",
        "stranded near Pir Sabaq - Urgent",
        "we need a boat - Send someone",
        "no food left - Thanks",
    ],
)
def test_a_plea_after_a_dash_is_not_treated_as_a_name(
    redactor: Redactor, text: str
) -> None:
    """Redacting "Please" would corrupt the message the coordinator reads."""
    assert redactor.redact(text) == text


def test_hyphenated_words_are_untouched(redactor: Redactor) -> None:
    text = "water at chest-height in the street"
    assert redactor.redact(text) == text


def test_place_names_survive_redaction(redactor: Redactor) -> None:
    """Locations are what the geocoder needs; losing them breaks the pipeline."""
    text = "We are trapped at Mohib Banda near the boys school in Nowshera"
    out = redactor.redact(text)
    assert "Mohib Banda" in out
    assert "Nowshera" in out


def test_an_ordinary_message_is_returned_unchanged(redactor: Redactor) -> None:
    text = "Kheshgi Payan, pani bohot tez barh raha hai. 6 log hain."
    assert redactor.redact(text) == text


@pytest.mark.parametrize("text", ["", "   "])
def test_blank_input_is_safe(redactor: Redactor, text: str) -> None:
    assert redactor.redact(text) == text


# --- honorifics and known name shapes ---------------------------------------


def test_an_honorific_led_name_is_removed(redactor: Redactor) -> None:
    out = redactor.redact("Dr Fatima Iqbal is treating the injured")
    assert "Fatima" not in out


def test_a_name_with_a_regional_family_name_is_removed(redactor: Redactor) -> None:
    out = redactor.redact("Fatima Bibi is on the roof with three children")
    assert "Fatima Bibi" not in out
    assert "three children" in out, "the substance of the message must survive"


# --- the mapping is never persisted -----------------------------------------


def test_the_pseudonym_map_is_in_memory_only(redactor: Redactor) -> None:
    """There is deliberately no method that writes the mapping anywhere."""
    redactor.redact("call 0300-5550101")
    assert redactor.pseudonym_count == 1
    assert not any(
        hasattr(redactor, name) for name in ("save", "persist", "flush", "to_json")
    )


def test_clearing_forgets_everything(redactor: Redactor) -> None:
    redactor.redact("call 0300-5550101")
    redactor.clear()
    assert redactor.pseudonym_count == 0


def test_separate_redactors_do_not_share_state() -> None:
    a, b = Redactor(), Redactor()
    a.redact("call 0300-5550101")
    assert b.pseudonym_count == 0
