"""Protected storage for native protocol artifacts (mandates, receipts, credentials).

Artifacts are encrypted at rest (Fernet / AES-128-CBC + HMAC) and referenced by
digest from the watch timeline, so logs and API responses carry evidence
references without exposing reusable credentials.
"""
from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime
from typing import Any, Dict, Optional

from cryptography.fernet import Fernet
from sqlalchemy.engine import Connection

from common.util import digest_json

from . import repo


def derive_key(secret: str) -> bytes:
    if not secret:
        secret = "development-only-artifact-key"
    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())


class ArtifactVault:
    def __init__(self, secret: str):
        self._f = Fernet(derive_key(secret))

    def store(self, conn: Connection, kind: str, obj: Dict[str, Any], at: datetime, *, watch_id: Optional[str] = None, authorization_id: Optional[str] = None, attempt_id: Optional[str] = None) -> Dict[str, str]:
        digest = digest_json(obj)
        ciphertext = self._f.encrypt(json.dumps(obj, sort_keys=True).encode("utf-8")).decode("ascii")
        art_id = repo.insert_artifact(conn, kind, digest, ciphertext, at, watch_id=watch_id, authorization_id=authorization_id, attempt_id=attempt_id)
        return {"artifact_id": art_id, "kind": kind, "digest": digest}

    def load(self, conn: Connection, art_id: str) -> Optional[Dict[str, Any]]:
        row = repo.get_artifact(conn, art_id)
        if not row:
            return None
        return json.loads(self._f.decrypt(row["ciphertext"].encode("ascii")).decode("utf-8"))

    def load_many(self, conn: Connection, **filters) -> Dict[str, Dict[str, Any]]:
        out = {}
        for row in repo.list_artifacts(conn, **filters):
            out[row["id"]] = {"kind": row["kind"], "digest": row["digest"], "created_at": row["created_at"], "content": json.loads(self._f.decrypt(row["ciphertext"].encode("ascii")).decode("utf-8"))}
        return out
