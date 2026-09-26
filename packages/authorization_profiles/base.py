"""Profile-neutral interface used by the watch service and purchase coordinator."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from .keys import KeyRing, TrustStore


class ProfileError(Exception):
    def __init__(self, code: str, description: str = ""):
        super().__init__("%s: %s" % (code, description) if description else code)
        self.code = code
        self.description = description


@dataclass
class ConsentRequest:
    """What the Trusted Surface shows and the user delegates (plan §13)."""

    watch_id: str
    watch_version: int
    user_id: str
    agent_id: str
    merchants: List[Dict[str, str]]          # [{id, name, website}]
    acceptable_items: List[Dict[str, str]]   # [{id: sku, title, merchant_id}]
    quantity: int
    currency: str
    inclusive_max_minor: int                 # translated ceiling (plan §8)
    not_after: datetime                      # watch deadline
    issued_at: datetime
    payment_instrument: Dict[str, str]
    prompt_summary: str


@dataclass
class ConsentArtifact:
    profile: str
    profile_version: str
    reference: str                 # digest of the primary user-signed artifact
    consent_reference: str
    expires_at: datetime
    agent_key_id: str
    native: Dict[str, Any]         # profile-native serialized artifacts (protected at rest)
    summary: Dict[str, Any]        # human-reviewable normalised constraints


@dataclass
class PreparedPresentation:
    """Transaction-specific evidence bound to one merchant checkout."""

    profile: str
    reference: str                        # digest of the closed checkout evidence
    checkout_hash: str
    merchant_payload: Dict[str, Any]      # what goes into the UCP complete request
    payment_payload: Dict[str, Any]       # what goes to the (simulated) credential provider / MPP
    native: Dict[str, Any]


@dataclass
class VerificationResult:
    ok: bool
    code: Optional[str] = None
    description: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


class AuthorizationProfile:
    name: str = ""
    version: str = ""

    def __init__(self, keys: KeyRing, trust: TrustStore):
        self.keys = keys
        self.trust = trust

    # --- consent (Trusted Surface; deterministic code, not the LLM)
    def issue_consent(self, req: ConsentRequest) -> ConsentArtifact:  # pragma: no cover - interface
        raise NotImplementedError

    # --- agent side: bind to a concrete merchant checkout
    def prepare(self, consent: Dict[str, Any], checkout_jwt: str, checkout: Dict[str, Any], merchant: Dict[str, str], amount_minor: int, currency: str, now: datetime, audience_merchant: str) -> PreparedPresentation:  # pragma: no cover
        raise NotImplementedError

    # --- verifier side (merchant role)
    def verify_for_merchant(self, merchant_payload: Dict[str, Any], checkout_jwt: str, merchant: Dict[str, str], now: datetime) -> VerificationResult:  # pragma: no cover
        raise NotImplementedError

    # --- verifier side (credential provider / MPP role)
    def verify_for_payment(self, payment_payload: Dict[str, Any], checkout_hash: str, now: datetime) -> VerificationResult:  # pragma: no cover
        raise NotImplementedError
