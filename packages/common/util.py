from __future__ import annotations

import base64
import hashlib
import json
import secrets
import uuid
from typing import Any


def new_id(prefix: str) -> str:
    return "%s_%s" % (prefix, uuid.uuid4().hex[:20])


def random_token(nbytes: int = 16) -> str:
    return secrets.token_urlsafe(nbytes)


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def canonical_json(obj: Any) -> str:
    """Deterministic JSON used for digests and idempotency keys."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_b64url(data: bytes) -> str:
    return b64url_encode(hashlib.sha256(data).digest())


def digest_json(obj: Any) -> str:
    return sha256_b64url(canonical_json(obj).encode("utf-8"))


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
