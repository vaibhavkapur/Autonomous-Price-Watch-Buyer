"""Key material for the fixtures.

Signing keys live outside the model and outside ordinary application logs. In
development they are generated once into ``fixtures/authorizations/dev_keys.json``
(git-ignored); in tests they are generated in memory. Merchants and verifiers
receive only public JWKs via a :class:`TrustStore`.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from joserfc.jwk import ECKey

ROLE_KEYS = (
    "agent",            # Shopping Agent key (bound via cnf in open mandates / L2 mandates)
    "agent_provider",   # AP2 Trusted Agent Provider signing key (Trusted Surface)
    "vi_issuer",        # VI Layer-1 issuer (credential provider fixture)
    "user_device",      # VI Layer-2 user device key
    "credential_provider",  # simulated payment credential provider / MPP receipt signer
    "merchant_a",
    "merchant_b",
)


class KeyRing:
    """Holds private keys for the roles this process is allowed to sign for."""

    def __init__(self, keys: Dict[str, ECKey]):
        self._keys = keys

    @classmethod
    def generate(cls, roles=ROLE_KEYS) -> "KeyRing":
        keys = {}
        for role in roles:
            keys[role] = ECKey.generate_key("P-256", {"kid": "%s-key-1" % role.replace("_", "-"), "use": "sig", "alg": "ES256"})
        return cls(keys)

    @classmethod
    def load_or_create(cls, path: Path) -> "KeyRing":
        if path.exists():
            data = json.loads(path.read_text())
            return cls({role: ECKey.import_key(jwk) for role, jwk in data["keys"].items()})
        ring = cls.generate()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"warning": "DEVELOPMENT KEYS ONLY", "keys": {r: k.as_dict(private=True) for r, k in ring._keys.items()}}, indent=2))
        return ring

    def private(self, role: str) -> ECKey:
        return self._keys[role]

    def public_jwk(self, role: str) -> Dict[str, Any]:
        return self._keys[role].as_dict(private=False)

    def kid(self, role: str) -> str:
        return self._keys[role].as_dict(private=False)["kid"]

    def trust_store(self) -> "TrustStore":
        return TrustStore({role: self.public_jwk(role) for role in self._keys})

    def subset(self, roles) -> "KeyRing":
        return KeyRing({r: self._keys[r] for r in roles if r in self._keys})


class TrustStore:
    """Public keys a verifier trusts, keyed by role and by kid."""

    def __init__(self, public_jwks: Dict[str, Dict[str, Any]]):
        self.by_role = dict(public_jwks)
        self.by_kid = {jwk["kid"]: jwk for jwk in public_jwks.values() if "kid" in jwk}

    def key(self, role: str) -> ECKey:
        return ECKey.import_key(self.by_role[role])

    def key_by_kid(self, kid: str) -> Optional[ECKey]:
        jwk = self.by_kid.get(kid)
        return ECKey.import_key(jwk) if jwk else None

    def as_jwks(self, roles=None) -> Dict[str, Any]:
        keys = [jwk for role, jwk in self.by_role.items() if roles is None or role in roles]
        return {"keys": keys}

    def to_json(self) -> str:
        return json.dumps({"roles": self.by_role}, indent=2)

    @classmethod
    def from_json(cls, text: str) -> "TrustStore":
        return cls(json.loads(text)["roles"])
