"""The intake node: channel payloads in, a clean `HelpRequest` out.

Pure Python, no model. Two jobs:

1. Normalise the shape. A web form, a WhatsApp webhook and a pasted block of SMS
   text all become the same record.
2. Redact. Phone numbers and names are replaced with stable pseudonyms *here*,
   at the boundary, before the text reaches a model, the store, or the log.
   Nothing downstream has to remember to do it, because nothing downstream ever
   sees the original.
"""

from __future__ import annotations

import uuid
from typing import Any

from ...models.common import utcnow
from ...models.request import Channel, HelpRequest
from ...services.redaction import Redactor, get_redactor

MAX_TEXT_CHARS = 4000


def new_request_id() -> str:
    return f"r_{uuid.uuid4().hex[:10]}"


def new_trace_id() -> str:
    return f"t_{uuid.uuid4().hex[:16]}"


def normalise_payload(payload: dict[str, Any]) -> tuple[str, str | None, Channel | None]:
    """Pull text, photo key and channel out of any accepted inbound shape.

    The channel comes back as None when the payload did not state one, so the
    caller can pick a default appropriate to its own endpoint.
    """
    channel: Channel | None = payload.get("channel")

    # WhatsApp/Twilio-style webhooks nest the body; a form posts it flat. We
    # accept both shapes and document the webhook as untested against the real
    # provider -- see the README.
    nested = payload.get("message")
    text = (
        payload.get("text")
        or payload.get("Body")
        or payload.get("body")
        or (nested.get("text") if isinstance(nested, dict) else None)
    )
    photo = (
        payload.get("photo")
        or payload.get("photo_key")
        or payload.get("MediaUrl0")
    )
    return (str(text or "").strip(), str(photo) if photo else None, channel)


def build(
    text: str,
    *,
    channel: Channel = "form",
    photo_key: str | None = None,
    request_id: str | None = None,
    redactor: Redactor | None = None,
) -> HelpRequest:
    """Create a redacted `HelpRequest`. Raises ValueError on empty text."""
    cleaned = (text or "").strip()
    if not cleaned:
        raise ValueError("A help request must carry some text.")
    if len(cleaned) > MAX_TEXT_CHARS:
        cleaned = cleaned[:MAX_TEXT_CHARS]

    redacted = (redactor or get_redactor()).redact(cleaned)

    return HelpRequest(
        id=request_id or new_request_id(),
        channel=channel,
        raw_text=redacted,
        photo_key=photo_key,
        received_at=utcnow(),
        status="new",
        trace_id=new_trace_id(),
        node_history=["intake"],
    )


def split_bulk(blob: str, limit: int = 100) -> list[str]:
    """Split a pasted block into individual messages.

    Blank-line separated first; if that yields one lump, fall back to lines.
    Coordinators paste both shapes and neither is worth a dialogue box.
    """
    chunks = [c.strip() for c in blob.split("\n\n") if c.strip()]
    if len(chunks) <= 1:
        chunks = [line.strip() for line in blob.splitlines() if line.strip()]
    return chunks[:limit]
