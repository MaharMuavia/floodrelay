"""PII redaction hook.

Belt and braces. `intake` already redacts before anything is stored, so in the
normal path this hook has nothing left to find. It is registered anyway, on
every agent, because "nothing reaches the model unredacted" should be true by
construction rather than by everyone remembering to call the right function.

The pseudonym map lives in memory for the life of the process and is never
persisted. There is deliberately no method here that writes it anywhere.
"""

from __future__ import annotations

from typing import Any

from ...services.redaction import Redactor, get_redactor


class PIIRedactionHook:
    """Redacts message content on its way into the model."""

    def __init__(self, redactor: Redactor | None = None) -> None:
        self.redactor = redactor or get_redactor()
        self.redacted_messages = 0

    def register_hooks(self, registry: Any, **kwargs: Any) -> None:
        from strands.hooks import BeforeInvocationEvent

        registry.add_callback(BeforeInvocationEvent, self.before_invocation)

    def before_invocation(self, event: Any) -> None:
        messages = getattr(event, "messages", None)
        if not messages:
            return

        changed = False
        for message in messages:
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                text = block.get("text")
                if not isinstance(text, str) or not text:
                    continue
                cleaned = self.redactor.redact(text)
                if cleaned != text:
                    block["text"] = cleaned
                    changed = True

        if changed:
            self.redacted_messages += 1
            # Reassigning is what marks the field dirty for the SDK; the loop
            # above mutated in place, so this makes the intent explicit.
            event.messages = messages
