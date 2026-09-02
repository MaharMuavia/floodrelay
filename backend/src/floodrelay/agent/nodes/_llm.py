"""Model-call helpers shared by the nodes.

Prompts live in agent/prompts/*.md and are loaded at runtime, so they are
version-controlled and reviewable as text rather than buried in f-strings.

`complete_json` is deliberately tolerant on input and strict on output. Small
local models wrap JSON in prose, add code fences, emit trailing commas, and
sometimes think out loud first. None of that should take the pipeline down, so
we extract the first balanced object and let the caller validate it.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..models import Role, get_model

PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"

# deepseek-r1 and friends emit reasoning in <think> blocks before answering.
_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


@lru_cache(maxsize=32)
def load_prompt(name: str) -> str:
    path = PROMPT_DIR / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"No prompt file at {path}")
    return path.read_text(encoding="utf-8")


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Pull the first balanced JSON object out of a model response."""
    if not text:
        return None
    cleaned = _THINK.sub("", text).strip()

    fenced = _FENCE.search(cleaned)
    if fenced:
        cleaned = fenced.group(1).strip()

    start = cleaned.find("{")
    while start != -1:
        depth, in_string, escaped = 0, False, False
        for i in range(start, len(cleaned)):
            ch = cleaned[i]
            if escaped:
                escaped = False
                continue
            if ch == "\\":
                escaped = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    blob = cleaned[start : i + 1]
                    try:
                        parsed = json.loads(blob)
                    except json.JSONDecodeError:
                        # One repair pass for the most common small-model slip.
                        try:
                            parsed = json.loads(re.sub(r",\s*([}\]])", r"\1", blob))
                        except json.JSONDecodeError:
                            break
                    return parsed if isinstance(parsed, dict) else None
        start = cleaned.find("{", start + 1)
    return None


def warm(role: Role = "light") -> bool:
    """Load the model before real work starts, so the first request is not slow.

    Loading a 2-4 GB model costs more than the inference that follows it, and
    doing it deliberately keeps that cost out of a coordinator's first request.
    Failure here is not fatal -- it just means the first real call pays for it.
    """
    try:
        complete("Reply with OK.", "OK", role=role, retries=1)
        return True
    except Exception:
        return False


def complete(system: str, user: str, *, role: Role = "light", retries: int = 1) -> str:
    """One turn against the configured model. Returns raw text.

    Retries once on a provider-level failure, which covers a dropped connection
    or a briefly unavailable endpoint. It deliberately does *not* carry the
    weight of any known defect: the SDK's null-token-count crash is fixed at
    source in agent/ollama_compat.py, because a fault that recurs on every
    attempt turns a retry into nothing but doubled latency.
    """
    from strands import Agent

    last: Exception | None = None
    for _ in range(retries + 1):
        try:
            agent = Agent(
                model=get_model(role), system_prompt=system, callback_handler=None
            )
            return str(agent(user))
        except Exception as exc:
            last = exc
    raise last if last else RuntimeError("model call failed with no exception")


def complete_json(
    system: str, user: str, *, role: Role = "light", retries: int = 1
) -> tuple[dict[str, Any] | None, str]:
    """Ask for JSON and parse it. Returns (parsed_or_None, raw_text).

    On an unparseable reply we retry once with a blunter instruction. Small
    models very often comply the second time, and one retry is cheaper than
    losing the request to the failure path.
    """
    raw = complete(system, user, role=role)
    parsed = extract_json_object(raw)
    if parsed is not None or retries <= 0:
        return parsed, raw

    retry_user = (
        f"{user}\n\nYour previous reply could not be parsed as JSON. "
        f"Reply with the JSON object only. No prose, no code fence."
    )
    raw2 = complete(system, retry_user, role=role)
    return extract_json_object(raw2), raw2
