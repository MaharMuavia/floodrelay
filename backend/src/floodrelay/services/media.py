"""Photo storage.

S3 (or MinIO) when configured, the local filesystem otherwise. The adapter in
use is reported by /healthz so nobody has to guess where an uploaded photo went.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from ..config import get_settings

FALLBACK_DIR = Path(__file__).resolve().parents[3] / "seed" / "photos"


def _local_dir() -> Path:
    s = get_settings()
    target = Path(s.media_dir) if s.media_dir else FALLBACK_DIR
    target.mkdir(parents=True, exist_ok=True)
    return target


def backend_label() -> str:
    s = get_settings()
    if s.s3_endpoint:
        return f"s3({s.s3_endpoint})"
    return f"filesystem({_local_dir()})"


def store_photo(filename: str, body: bytes) -> str:
    """Persist an uploaded photo and return the key used to fetch it back."""
    suffix = Path(filename).suffix.lower() or ".jpg"
    key = f"{uuid.uuid4().hex[:12]}{suffix}"

    s = get_settings()
    # Use object storage when one is actually configured: an explicit S3/MinIO
    # endpoint, or a real AWS deployment with no local media dir overriding it.
    use_object_store = bool(s.s3_endpoint) or (s.ddb_endpoint is None and not s.media_dir)
    if use_object_store:
        try:
            import boto3

            client = boto3.client(
                "s3", region_name=s.aws_region, endpoint_url=s.s3_endpoint or None
            )
            client.put_object(Bucket=s.s3_bucket, Key=key, Body=body)
            return key
        except Exception:
            # Fall through to the filesystem rather than losing the upload.
            pass

    (_local_dir() / key).write_bytes(body)
    return key


def load_photo(key: str) -> bytes | None:
    path = _local_dir() / Path(key).name
    if path.is_file():
        return path.read_bytes()
    fallback = FALLBACK_DIR / Path(key).name
    return fallback.read_bytes() if fallback.is_file() else None
