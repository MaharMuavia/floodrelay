"""Null-safe handling of Ollama's usage metadata.

Ollama returns `prompt_eval_count`, `eval_count` and `total_duration` as null on
some responses. The SDK provider adds and divides them without checking, which
raised TypeError and killed the agent invocation -- reproducibly, on one seed
message, across two end-to-end runs.

These tests describe the behaviour we need rather than the shim itself, so they
still pass if the upstream defect is fixed and the shim is deleted.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from floodrelay.agent.ollama_compat import format_metadata_chunk


@dataclass
class FakeUsage:
    """The shape Ollama's client hands the provider."""

    prompt_eval_count: int | None = None
    eval_count: int | None = None
    total_duration: int | None = None


def test_a_fully_populated_response_is_reported_accurately() -> None:
    chunk = format_metadata_chunk(
        FakeUsage(prompt_eval_count=990, eval_count=89, total_duration=24_800_000_000)
    )
    usage = chunk["metadata"]["usage"]
    assert usage["inputTokens"] == 990
    assert usage["outputTokens"] == 89
    assert usage["totalTokens"] == 1079
    assert chunk["metadata"]["metrics"]["latencyMs"] == 24_800


def test_all_null_counts_do_not_raise() -> None:
    """This is the exact payload that produced the 700-second outlier."""
    chunk = format_metadata_chunk(FakeUsage())
    usage = chunk["metadata"]["usage"]
    assert usage == {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0}
    assert chunk["metadata"]["metrics"]["latencyMs"] == 0


@pytest.mark.parametrize(
    ("prompt", "output", "expected_total"),
    [
        (None, 89, 89),
        (990, None, 990),
        (0, 0, 0),
    ],
)
def test_a_partially_null_response_counts_what_it_can(
    prompt: int | None, output: int | None, expected_total: int
) -> None:
    usage = format_metadata_chunk(
        FakeUsage(prompt_eval_count=prompt, eval_count=output, total_duration=1_000_000)
    )["metadata"]["usage"]
    assert usage["totalTokens"] == expected_total


def test_a_null_duration_does_not_divide_by_none() -> None:
    chunk = format_metadata_chunk(FakeUsage(prompt_eval_count=5, eval_count=5))
    assert chunk["metadata"]["metrics"]["latencyMs"] == 0


def test_missing_attributes_entirely_are_tolerated() -> None:
    """A client version that drops a field must not take the pipeline down."""

    class Bare:
        pass

    chunk = format_metadata_chunk(Bare())
    assert chunk["metadata"]["usage"]["totalTokens"] == 0
