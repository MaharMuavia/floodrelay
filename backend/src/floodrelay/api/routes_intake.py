"""Intake: web form, bulk paste, photo, and the WhatsApp/SMS webhook shape.

Every route returns 202 and queues the work. A person sending a help request
should never wait on a 7B model running on someone's laptop.

Webhook note: `/intake/webhook` accepts the shape a WhatsApp Business or Twilio
callback posts, but it has **not** been tested against the real provider -- there
is no signature verification and no delivery-receipt handling. It is documented
as untested in the README rather than half-built and presented as working.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

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


@router.post("/intake/webhook", status_code=status.HTTP_202_ACCEPTED)
def webhook(payload: dict[str, Any], service: PipelineDep) -> dict[str, Any]:
    """Accepts a WhatsApp/Twilio-style callback. Untested against the real provider."""
    text, photo, channel = intake_node.normalise_payload(payload)
    if not text:
        raise HTTPException(status_code=422, detail="no message body found in the payload")

    request = service.accept(text, channel=channel or "whatsapp", photo_key=photo)
    service.submit(request)
    return {
        "request_id": request.id,
        "trace_id": request.trace_id,
        "note": "This endpoint is not verified against a real WhatsApp Business account.",
    }
