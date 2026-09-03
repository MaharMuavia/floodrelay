"""Urgency scoring: pure, table-driven, no model involved.

If these tests pass, the priority number on the board is arithmetic the
coordinator can audit -- which is the whole point of keeping it out of the LLM.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from conftest import FIXED_NOW, make_need
from floodrelay.services.scoring import (
    KIND_WEIGHT,
    WEIGHTS,
    compute_urgency,
    recency_score,
    vulnerability_score,
    water_level_signal,
)


def test_weights_sum_to_one() -> None:
    assert sum(WEIGHTS.values()) == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("rescue", 1.0),
        ("medical", 0.9),
        ("shelter", 0.6),
        ("food_water", 0.4),
        ("other", 0.2),
    ],
)
def test_kind_weights_match_the_published_table(kind: str, expected: float) -> None:
    assert KIND_WEIGHT[kind] == expected  # type: ignore[index]


# --- vulnerability ---------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, 0.0),
        ({"children": 1}, 0.20),
        ({"children": 2}, 0.40),
        ({"children": 9}, 0.40),  # capped
        ({"elderly": 1}, 0.15),
        ({"elderly": 5}, 0.30),  # capped
        ({"disabled": True}, 0.25),
        ({"pregnant": True}, 0.25),
        ({"people_total": 6}, 0.15),
        ({"people_total": 4}, 0.0),  # below the "large group" threshold
        ({"children": 3, "elderly": 2, "disabled": True, "pregnant": True}, 1.0),  # capped
    ],
)
def test_vulnerability_is_capped_and_additive(
    overrides: dict[str, object], expected: float
) -> None:
    assert vulnerability_score(make_need(**overrides)) == pytest.approx(expected)


def test_unstated_counts_contribute_nothing() -> None:
    """None means the message did not say, which is not the same as zero."""
    assert vulnerability_score(make_need(children=None, elderly=None)) == 0.0


# --- water level -----------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (None, 0.0),
        ("", 0.0),
        ("we are on the roof", 1.0),
        ("chhat par hain", 1.0),
        ("water at chest height", 0.85),
        ("pani seene tak aa gaya", 0.85),
        ("water up to my waist", 0.65),
        ("kamar tak pani", 0.65),
        ("knee deep", 0.45),
        ("ghutne tak", 0.45),
        ("ankle deep only", 0.25),
        ("the road is wet", 0.0),
    ],
)
def test_water_level_signal_english_and_roman_urdu(text: str | None, expected: float) -> None:
    assert water_level_signal(text) == pytest.approx(expected)


def test_water_level_takes_the_strongest_signal_present() -> None:
    """A message mentioning both takes the worse reading, not the first one."""
    assert water_level_signal("ankle deep outside but chest height inside") == pytest.approx(0.85)


# --- recency ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (timedelta(0), 1.0),
        (timedelta(hours=3), 0.5),
        (timedelta(hours=6), 0.0),
        (timedelta(hours=24), 0.0),  # floored, never negative
    ],
)
def test_recency_decays_linearly_then_floors(age: timedelta, expected: float) -> None:
    assert recency_score(FIXED_NOW - age, FIXED_NOW) == pytest.approx(expected)


def test_future_timestamps_do_not_exceed_one() -> None:
    assert recency_score(FIXED_NOW + timedelta(hours=1), FIXED_NOW) == 1.0


# --- the whole formula -----------------------------------------------------


def test_worst_case_scores_one() -> None:
    need = make_need(
        kind="rescue", children=3, elderly=2, disabled=True, pregnant=True,
        water_level_note="on the roof, water still rising",
    )
    result = compute_urgency(
        need, photo_severity=1.0, received_at=FIXED_NOW, now=FIXED_NOW
    )
    assert result.total == pytest.approx(1.0)


def test_a_calm_food_request_scores_low() -> None:
    result = compute_urgency(
        make_need(kind="food_water"),
        raw_text="we could use drinking water when convenient",
        received_at=FIXED_NOW,
        now=FIXED_NOW,
    )
    # 0.40*0.4 kind + 0 vulnerability + 0 photo + 0 water + 0.05*1.0 recency
    assert result.total == pytest.approx(0.21)


def test_breakdown_components_sum_to_the_total() -> None:
    need = make_need(kind="medical", children=1, elderly=1)
    r = compute_urgency(
        need, raw_text="water at waist height", photo_severity=0.5,
        received_at=FIXED_NOW - timedelta(hours=3), now=FIXED_NOW,
    )
    assert r.kind + r.vulnerability + r.photo + r.water_level + r.recency == pytest.approx(r.total)


def test_rescue_outranks_food_all_else_equal() -> None:
    rescue = compute_urgency(make_need(kind="rescue"), received_at=FIXED_NOW, now=FIXED_NOW)
    food = compute_urgency(make_need(kind="food_water"), received_at=FIXED_NOW, now=FIXED_NOW)
    assert rescue.total > food.total


def test_photo_severity_only_counts_when_a_photo_exists() -> None:
    without = compute_urgency(
        make_need(), photo_severity=None, received_at=FIXED_NOW, now=FIXED_NOW
    )
    with_photo = compute_urgency(
        make_need(), photo_severity=0.8, received_at=FIXED_NOW, now=FIXED_NOW
    )
    assert without.photo == 0.0
    assert with_photo.photo == pytest.approx(0.20 * 0.8)


def test_unextracted_request_still_scores_on_recency_alone() -> None:
    """Unprocessed items must sort sensibly, not sink to the bottom at zero."""
    r = compute_urgency(None, received_at=FIXED_NOW, now=FIXED_NOW)
    assert r.total == pytest.approx(WEIGHTS["recency"])


def test_result_never_leaves_the_unit_interval() -> None:
    need = make_need(kind="rescue", children=99, elderly=99, disabled=True, pregnant=True,
                     people_total=500, water_level_note="roof, rising fast")
    r = compute_urgency(need, photo_severity=1.0, received_at=FIXED_NOW, now=FIXED_NOW)
    assert 0.0 <= r.total <= 1.0


# ---------------------------------------------------------------------------
# The line between context and arithmetic
# ---------------------------------------------------------------------------
#
# Real satellite imagery, river discharge, NDMA damage figures and ReliefWeb
# headlines were added to this project as *context*. Every one of them is a
# plausible-looking number that a future change could quietly fold into the
# urgency score, and the moment that happens urgency stops being deterministic
# and stops being explainable to the coordinator it is shown to.
#
# Comments do not enforce that. These tests do.

CONTEXT_SOURCES = {
    "river",
    "ndma",
    "imagery",
    "imagery_layers",
    "reliefweb",
    "weather",
    "places",
    "routing",
}


def test_scoring_imports_no_context_source() -> None:
    """scoring.py must stay pure: no network, no imagery, no situation data."""
    import ast
    from pathlib import Path

    from floodrelay.services import scoring as scoring_module

    tree = ast.parse(Path(scoring_module.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module:
                imported.update(node.module.split("."))
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)

    leaked = imported & CONTEXT_SOURCES
    assert not leaked, (
        f"scoring.py imports context source(s) {sorted(leaked)}. Urgency must be "
        "computable from the message alone."
    )


def test_compute_urgency_accepts_no_context_arguments() -> None:
    """The signature is the contract. A `river_discharge=` parameter appearing
    here would mean the flood layer had started moving priorities."""
    import inspect

    from floodrelay.services import scoring as scoring_module

    parameters = set(inspect.signature(scoring_module.compute_urgency).parameters)

    assert parameters == {"need", "raw_text", "photo_severity", "received_at", "now"}


def test_weights_still_sum_to_one() -> None:
    """If a context component were ever added as a weighted term, this breaks."""
    from floodrelay.services import scoring as scoring_module

    assert round(sum(scoring_module.WEIGHTS.values()), 6) == 1.0
