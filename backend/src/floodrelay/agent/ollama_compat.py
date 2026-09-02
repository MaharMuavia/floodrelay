"""Compatibility shim for the Strands Ollama provider.

`strands.models.ollama.OllamaModel.format_chunk` builds the metadata chunk as:

    "totalTokens": event["data"].eval_count + event["data"].prompt_eval_count
    "latencyMs":   int(event["data"].total_duration / 1e6)

Ollama returns all three of those fields as `null` on some responses -- reliably
on the chunk emitted while a model is being loaded, and intermittently on
ordinary completions. Both expressions then raise `TypeError`, which kills the
whole agent invocation.

This was not a theoretical concern. It produced a reproducible 700-second
outlier on one seed message across two end-to-end runs: the failure surfaced
several layers up, every retry hit the same null response and failed again, and
the retries simply multiplied the cost of failing.

Retrying was the wrong fix, because the condition recurs rather than clearing.
The right fix is to make the provider tolerate the nulls, which is all this
subclass does. Remove it when the upstream defect is fixed; `test_ollama_compat`
will still pass against a corrected provider.
"""

from __future__ import annotations

from typing import Any


def _count(data: Any, field: str) -> int:
    """A missing count is zero, not a crash. `or 0` also handles a real 0."""
    return getattr(data, field, None) or 0


def build_safe_ollama_model() -> type:
    """Return an `OllamaModel` subclass that tolerates null usage figures.

    Built lazily inside a function so that importing this module does not
    require the optional `ollama` dependency.
    """
    from strands.models.ollama import OllamaModel

    class SafeOllamaModel(OllamaModel):
        """OllamaModel that will not crash on a response with no token counts."""

        def format_chunk(self, event: dict[str, Any]) -> Any:
            if event.get("chunk_type") != "metadata":
                return super().format_chunk(event)

            data = event["data"]
            input_tokens = _count(data, "prompt_eval_count")
            output_tokens = _count(data, "eval_count")
            total_duration = _count(data, "total_duration")

            return {
                "metadata": {
                    "usage": {
                        "inputTokens": input_tokens,
                        "outputTokens": output_tokens,
                        "totalTokens": input_tokens + output_tokens,
                    },
                    "metrics": {"latencyMs": int(total_duration / 1e6)},
                },
            }

    return SafeOllamaModel


def format_metadata_chunk(data: Any) -> dict[str, Any]:
    """The null-safe metadata mapping, exposed for testing without an SDK import."""
    input_tokens = _count(data, "prompt_eval_count")
    output_tokens = _count(data, "eval_count")
    total_duration = _count(data, "total_duration")
    return {
        "metadata": {
            "usage": {
                "inputTokens": input_tokens,
                "outputTokens": output_tokens,
                "totalTokens": input_tokens + output_tokens,
            },
            "metrics": {"latencyMs": int(total_duration / 1e6)},
        },
    }
