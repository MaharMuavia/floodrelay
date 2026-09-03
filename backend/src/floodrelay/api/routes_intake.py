"""Intake: web form, bulk paste, photo, and the WhatsApp/SMS webhook shape.

Every route returns 202 and queues the work. A person sending a help request
should never wait on a 7B model running on someone's laptop.

Webhook note, precisely: `/intake/webhook` verifies an `X-Hub-Signature-256`
HMAC over the raw body and refuses anything unsigned, including when no secret
is configured. What is still unverified is the payload *shape* -- this has never
run against a live WhatsApp Business account, so `normalise_payload` is written
from the documented format rather than an observed one. Delivery receipts and
status callbacks are not handled at all.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
    status,
)

from ..agent.nodes import intake as intake_node
from .deps import (
    ALLOWED_PHOTO_TYPES,
    MAX_BULK_ITEMS,
    MAX_PHOTO_BYTES,
    BulkIn,
    BulkOut,
    IntakeIn,
    IntakeOut,
    PipelineDep,
    SettingsDep,
)

router = APIRouter(tags=["intake"])


@router.post("/intake", status_code=status.HTTP_202_ACCEPTED, response_model=IntakeOut)
def create_intake(payload: IntakeIn, service: PipelineDep) -> IntakeOut:
    request = service.accept(payload.text, channel=payload.channel, photo_key=payload.photo)
    service.submit(request)
    return IntakeOut(request_id=request.id, trace_id=request.trace_id)


@router.post("/intake/bulk", status_code=status.HTTP_202_ACCEPTED, response_model=BulkOut)
def create_bulk(payload: BulkIn, service: PipelineDep) -> BulkOut:
    ids: list[str] = []
    for item in payload.items:
        request = service.accept(item.text, channel=item.channel)
        service.submit(request)
        ids.append(request.id)
    return BulkOut(accepted=len(ids), request_ids=ids)


@router.post("/intake/paste", status_code=status.HTTP_202_ACCEPTED, response_model=BulkOut)
def create_from_paste(payload: dict[str, str], service: PipelineDep) -> BulkOut:
    """Split a pasted block into messages, one per blank-line-separated chunk."""
    blob = (payload.get("text") or "").strip()
    if not blob:
        raise HTTPException(status_code=422, detail="text must not be empty")

    chunks = intake_node.split_bulk(blob, limit=MAX_BULK_ITEMS)
    if not chunks:
        raise HTTPException(status_code=422, detail="nothing to import from that paste")

    ids: list[str] = []
    for chunk in chunks:
        request = service.accept(chunk, channel="bulk_paste")
        service.submit(request)
        ids.append(request.id)
    return BulkOut(accepted=len(ids), request_ids=ids)


@router.post("/intake/photo", status_code=status.HTTP_202_ACCEPTED, response_model=IntakeOut)
async def create_with_photo(
    service: PipelineDep,
    text: str = Form(..., min_length=1, max_length=4000),
    photo: UploadFile = File(...),
) -> IntakeOut:
    if photo.content_type not in ALLOWED_PHOTO_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"photo must be one of {sorted(ALLOWED_PHOTO_TYPES)}, got {photo.content_type}",
        )

    body = await photo.read(MAX_PHOTO_BYTES + 1)
    if len(body) > MAX_PHOTO_BYTES:
        raise HTTPException(status_code=413, detail="photo must be 8 MB or smaller")
    if not body:
        raise HTTPException(status_code=422, detail="photo was empty")

    from ..services.media import store_photo

    key = store_photo(photo.filename or "upload.jpg", body)
    request = service.accept(text, channel="form", photo_key=key)
    service.submit(request)
    return IntakeOut(request_id=request.id, trace_id=request.trace_id)


def verify_signature(secret: str | None, body: bytes, header: str | None) -> None:
    """Check the `X-Hub-Signature-256` HMAC over the raw request body, or refuse.

    Fail-closed in every direction, for the same reason the dispatch gate is: an
    unauthenticated public webhook is a queue anybody can fill, and a queue
    anybody can fill is a coordinator whose real calls are buried.

    * No secret configured -> 503. The route is off, and says so.
    * No signature, or a malformed one -> 401.
    * A signature that does not match the body -> 401.

    Meta's format is `sha256=<hex digest>` over the exact bytes received, so the
    body must be read raw. Re-serialising the parsed JSON would change
    whitespace and key order and never match.
    """
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "The webhook is not configured. Set WEBHOOK_SECRET to the app secret "
                "of the WhatsApp Business account that will post here."
            ),
        )

    if not header or not header.startswith("sha256="):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed X-Hub-Signature-256 header.",
        )

    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(header.removeprefix("sha256="), expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Signature does not match the request body.",
        )


@router.post("/intake/webhook", status_code=status.HTTP_202_ACCEPTED)
async def webhook(
    request: Request,
    service: PipelineDep,
    settings: SettingsDep,
    x_hub_signature_256: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """Accepts a signed WhatsApp/Twilio-style callback.

    The signature check is real and tested. What is still unverified is the
    *shape*: this has never run against a live WhatsApp Business account, so the
    payload parsing in `intake.normalise_payload` is written from the documented
    format rather than from an observed one.
    """
    body = await request.body()
    verify_signature(settings.webhook_secret, body, x_hub_signature_256)

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"body is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="body must be a JSON object")

    text, photo, channel = intake_node.normalise_payload(payload)
    if not text:
        raise HTTPException(status_code=422, detail="no message body found in the payload")

    help_request = service.accept(text, channel=channel or "whatsapp", photo_key=photo)
    service.submit(help_request)
    return {
        "request_id": help_request.id,
        "trace_id": help_request.trace_id,
        "note": "Signature verified. The payload shape is not verified against a live account.",
    }
