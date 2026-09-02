"""Resource contention: the two seeded rescue calls must contend for one boat."""

from __future__ import annotations

from datetime import timedelta

from conftest import FIXED_NOW, make_request
from floodrelay.services.conflict import contention_for, find_contentions


def _matched(request_id: str, resource_id: str, urgency: float, minutes: int = 0):
    return make_request(
        request_id,
        status="matched",
        matched_resource_id=resource_id,
        urgency=urgency,
        updated_at=FIXED_NOW + timedelta(minutes=minutes),
    )


def test_two_rescues_on_one_boat_are_a_conflict() -> None:
    requests = [
        _matched("r_03", "res_boat_1", 0.91),
        _matched("r_28", "res_boat_1", 0.87, minutes=6),
    ]
    conflicts = find_contentions(requests)
    assert len(conflicts) == 1
    assert conflicts[0].resource_id == "res_boat_1"
    assert conflicts[0].is_conflict
    # Most urgent first, so the card can present the agent's pick as option A.
    assert conflicts[0].request_ids == ["r_03", "r_28"]


def test_distinct_resources_do_not_contend() -> None:
    requests = [
        _matched("r_03", "res_boat_1", 0.91),
        _matched("r_18", "res_med_1", 0.88),
    ]
    assert find_contentions(requests) == []


def test_a_single_match_is_not_a_conflict() -> None:
    assert find_contentions([_matched("r_03", "res_boat_1", 0.91)]) == []


def test_dispatched_requests_are_spent_not_contended() -> None:
    """A boat already sent is not being competed for."""
    requests = [
        make_request(
            "r_03", status="dispatched", matched_resource_id="res_boat_1", urgency=0.91
        ),
        _matched("r_28", "res_boat_1", 0.87),
    ]
    assert find_contentions(requests) == []


def test_closed_and_duplicate_requests_are_excluded() -> None:
    requests = [
        make_request("r_03", status="closed", matched_resource_id="res_boat_1", urgency=0.9),
        make_request(
            "r_29", status="duplicate", matched_resource_id="res_boat_1", urgency=0.9,
            duplicate_of="r_01",
        ),
        _matched("r_28", "res_boat_1", 0.87),
    ]
    assert find_contentions(requests) == []


def test_stale_matches_outside_the_window_do_not_contend() -> None:
    requests = [
        _matched("r_03", "res_boat_1", 0.91, minutes=0),
        _matched("r_28", "res_boat_1", 0.87, minutes=600),  # ten hours later
    ]
    assert find_contentions(requests, window=timedelta(hours=2)) == []


def test_three_way_contention_is_reported_as_one_group() -> None:
    requests = [
        _matched("r_03", "res_boat_1", 0.91),
        _matched("r_28", "res_boat_1", 0.87, minutes=5),
        _matched("r_40", "res_boat_1", 0.95, minutes=9),
    ]
    conflicts = find_contentions(requests)
    assert len(conflicts) == 1
    assert conflicts[0].request_ids == ["r_40", "r_03", "r_28"]


def test_contention_for_finds_the_named_resource() -> None:
    requests = [
        _matched("r_03", "res_boat_1", 0.91),
        _matched("r_28", "res_boat_1", 0.87, minutes=3),
    ]
    assert contention_for(requests, "res_boat_1") is not None
    assert contention_for(requests, "res_med_1") is None


def test_unmatched_requests_are_ignored() -> None:
    assert find_contentions([make_request("r_10", status="new", urgency=0.4)]) == []
