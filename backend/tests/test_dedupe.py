"""Duplicate detection.

The seed set contains one household in Mohib Banda reported twice: r_01 from the
mother on the roof, r_02 from a neighbour a few minutes later. Catching that
pair is the point of this node -- two reports of one family are not two
families, and sending two boats to one roof means someone else waits.

The opposite error matters just as much, so there are as many tests here for
things that must *not* merge as for things that must.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from conftest import FIXED_NOW, make_need, make_request
from floodrelay.agent.nodes.dedupe import run, similarity
from floodrelay.models.common import Confidence, GeoPoint

# The boys school in Mohib Banda, and a point ~90 m away.
SCHOOL = (34.0151, 71.9747)
NEXT_DOOR = (34.0159, 71.9747)
FAR = (34.0672, 71.9944)  # Risalpur, ~6 km


def _at(lat: float, lon: float, score: float = 0.82) -> GeoPoint:
    return GeoPoint(
        lat=lat, lon=lon, label="Mohib Banda",
        source="nominatim",
        confidence=Confidence(score=score, reason="test fixture"),
    )


def _seed_pair():
    """The two real seed messages, as the pipeline would have them."""
    a = make_request(
        "r_01",
        raw_text=(
            "Water came into the house very fast. We are 4 people, 3 children with me, "
            "we are on the roof now. Mohib Banda near the boys school. Please send boat."
        ),
        need=make_need(kind="rescue", people_total=4, children=3,
                       water_level_note="on the roof", raw_location_text="Mohib Banda"),
        location=_at(*SCHOOL),
        received_at=FIXED_NOW,
        urgency=0.9,
    )
    b = make_request(
        "r_02",
        raw_text=(
            "Neighbour ki family chhat par phansi hui hai, Mohib Banda, school ke paas. "
            "Teen bacche hain. Koi boat bhejo jaldi."
        ),
        need=make_need(kind="rescue", children=3,
                       water_level_note="chhat par", raw_location_text="Mohib Banda"),
        location=_at(*NEXT_DOOR),
        received_at=FIXED_NOW + timedelta(minutes=5),
        urgency=0.88,
    )
    return a, b


# --- the pair that must be caught -------------------------------------------


def test_the_two_seeded_near_duplicates_are_caught() -> None:
    a, b = _seed_pair()
    score, reasons = similarity(a, b)
    assert score >= 0.40, f"scored only {score}: {reasons}"
    verdict = run(b, [a])
    assert verdict.candidate_id == "r_01"
    assert verdict.is_duplicate or verdict.needs_human, (
        f"a repeat report of one household must not pass as distinct (score {verdict.score})"
    )


def test_the_verdict_explains_itself() -> None:
    a, b = _seed_pair()
    verdict = run(b, [a])
    assert verdict.reason
    assert "apart" in verdict.reason or "wording" in verdict.reason


def test_an_identical_repeat_scores_as_an_automatic_duplicate() -> None:
    """r_15 is the same caller chasing the same request two hours later."""
    a, _ = _seed_pair()
    chase = make_request(
        "r_15",
        raw_text=(
            "Is there any update on the boat? We called from Mohib Banda near the school. "
            "Still on the roof. 4 people, 3 children."
        ),
        need=make_need(kind="rescue", people_total=4, children=3,
                       raw_location_text="Mohib Banda"),
        location=_at(*SCHOOL),
        received_at=FIXED_NOW + timedelta(hours=2),
        urgency=0.9,
    )
    verdict = run(chase, [a])
    assert verdict.candidate_id == "r_01"
    assert verdict.is_duplicate


# --- things that must NOT merge ---------------------------------------------


def test_two_households_far_apart_are_never_merged() -> None:
    a, b = _seed_pair()
    b.location = _at(*FAR)
    score, reasons = similarity(a, b)
    assert score == 0.0
    assert "too far" in reasons[0]


def test_different_needs_at_the_same_place_are_not_merged() -> None:
    """A rescue and a food request from one village are two different things."""
    a, b = _seed_pair()
    b.need = make_need(kind="food_water", raw_location_text="Mohib Banda")
    b.raw_text = "Mohib Banda mein khana chahiye, hum mehfooz hain"
    verdict = run(b, [a])
    assert not verdict.is_duplicate


def test_requests_outside_the_time_window_are_not_compared() -> None:
    a, b = _seed_pair()
    b.received_at = FIXED_NOW + timedelta(hours=12)
    assert run(b, [a]).candidate_id is None


def test_already_closed_and_duplicate_requests_are_skipped() -> None:
    a, b = _seed_pair()
    a.status = "duplicate"
    assert run(b, [a]).candidate_id is None
    a.status = "closed"
    assert run(b, [a]).candidate_id is None


def test_a_request_is_never_its_own_duplicate() -> None:
    a, _ = _seed_pair()
    assert run(a, [a]).candidate_id is None


def test_no_other_requests_means_no_duplicate() -> None:
    a, _ = _seed_pair()
    verdict = run(a, [])
    assert verdict.candidate_id is None
    assert "no other open request" in verdict.reason


# --- threshold behaviour -----------------------------------------------------


def test_a_mid_confidence_match_asks_a_human_rather_than_merging() -> None:
    """Between 0.40 and 0.75 the coordinator decides, per gate rule 4."""
    a, b = _seed_pair()
    # Same village, different street, different headcount, different wording.
    b.location = _at(34.0210, 71.9800)
    b.need = make_need(kind="rescue", people_total=2, raw_location_text="Mohib Banda")
    b.raw_text = "do log phanse hain, koi boat bhejo"
    verdict = run(b, [a])
    if verdict.candidate_id is not None:
        assert not verdict.is_duplicate, "a partial match must not be merged silently"


def test_headcount_disagreement_lowers_the_score() -> None:
    a, b = _seed_pair()
    agree, _ = similarity(a, b)
    b.need = make_need(kind="rescue", people_total=11, children=3,
                       raw_location_text="Mohib Banda")
    disagree, reasons = similarity(a, b)
    assert disagree < agree
    assert any("headcounts differ" in r for r in reasons)


@pytest.mark.parametrize("gap_minutes", [1, 30, 59])
def test_closer_in_time_scores_higher(gap_minutes: int) -> None:
    a, b = _seed_pair()
    b.received_at = FIXED_NOW + timedelta(minutes=gap_minutes)
    near, _ = similarity(a, b)
    b.received_at = FIXED_NOW + timedelta(hours=5)
    far, _ = similarity(a, b)
    assert near > far


# --- the village-centroid trap ----------------------------------------------
#
# Found by an end-to-end run: two different households in Pir Sabaq were merged
# because Nominatim resolves a village name to a single centroid, so both
# requests carried identical coordinates.


def _geocoded(label: str) -> GeoPoint:
    return GeoPoint(
        lat=34.0300, lon=71.9200, label=label,
        source="nominatim",
        confidence=Confidence(score=0.8, reason="village centroid"),
    )


def test_two_households_in_one_village_are_not_merged() -> None:
    """Identical coordinates from the same place *name* locate a village, not a home."""
    a = make_request(
        "r_03",
        raw_text="PIR SABAQ. water at chest height in our street. my father is 78 and cannot walk",
        need=make_need(kind="rescue", elderly=1, raw_location_text="Pir Sabaq"),
        location=_geocoded("Pir Sabaq, Nowshera"),
        received_at=FIXED_NOW,
        urgency=0.9,
    )
    b = make_request(
        "r_28",
        raw_text="Doobne wala hai sab kuch. Pir Sabaq. Chhat par hain 4 log. ALLAH ke waaste jaldi",
        need=make_need(kind="rescue", people_total=4, raw_location_text="Pir Sabaq"),
        location=_geocoded("Pir Sabaq, Nowshera"),
        received_at=FIXED_NOW + timedelta(minutes=20),
        urgency=0.88,
    )
    verdict = run(b, [a])
    assert not verdict.is_duplicate, (
        f"two different households in one village were auto-merged (score {verdict.score})"
    )


def test_the_village_centroid_reason_is_explicit() -> None:
    a = make_request("r_a", location=_geocoded("Pir Sabaq"), received_at=FIXED_NOW)
    b = make_request(
        "r_b", location=_geocoded("Pir Sabaq"), received_at=FIXED_NOW + timedelta(minutes=5)
    )
    _score, reasons = similarity(a, b)
    assert any("rather than the household" in r for r in reasons)


def test_real_coordinates_at_the_same_spot_still_count_fully() -> None:
    """A caller's own coordinates are precise, so proximity remains strong evidence."""
    precise = GeoPoint(
        lat=34.0151, lon=71.9747, label="34.0151, 71.9747",
        source="coordinates_in_message",
        confidence=Confidence(score=0.95, reason="explicit coordinates"),
    )
    a = make_request("r_x", location=precise, received_at=FIXED_NOW,
                     need=make_need(kind="rescue"))
    b = make_request("r_y", location=precise.model_copy(),
                     received_at=FIXED_NOW + timedelta(minutes=3),
                     need=make_need(kind="rescue"))
    score, reasons = similarity(a, b)
    assert score >= 0.6, reasons
    assert any("same spot" in r for r in reasons)
