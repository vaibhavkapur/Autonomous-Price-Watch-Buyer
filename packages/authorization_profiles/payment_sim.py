"""Simulated Credential Provider / Network / Merchant Payment Processor.

Real signing and verification happen here; only the movement of money is
simulated. One fixture plays the CP + Network roles (payment mandate verifier
that returns a payment credential and a Payment Receipt) and the MPP role
(verifies the credential is scoped to the checkout and "captures" it).
"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Any, Dict, Optional, Set

from common.util import new_id

from . import sdjwt
from .ap2 import make_receipt
from .base import VerificationResult
from .keys import KeyRing, TrustStore
from .registry import get_profile

CP_ISSUER = "https://credential-provider.fixture.pricewatch.local"


class CredentialProviderSim:
    def __init__(self, keys: KeyRing, trust: TrustStore):
        self.keys = keys
        self.trust = trust
        self._lock = threading.Lock()
        self._used_pairs: Set[str] = set()      # VI: one L3a+L3b pair per L2 pair
        self._used_references: Set[str] = set()  # AP2: closed payment mandate references seen
        self.receipts: Dict[str, str] = {}

    def authorize(self, profile_name: str, payment_payload: Dict[str, Any], checkout_hash: str, now: datetime) -> Dict[str, Any]:
        profile = get_profile(profile_name, self.keys, self.trust)
        result: VerificationResult = profile.verify_for_payment(payment_payload, checkout_hash, now)
        cp_key = self.keys.private("credential_provider")
        payment_id = new_id("pay")
        if result.ok:
            with self._lock:
                pair_id = result.details.get("l2_pair_id")
                if pair_id and pair_id in self._used_pairs:
                    result = VerificationResult(False, "invalid_mandate", "an L3 pair was already presented for this L2 mandate pair")
                elif result.details["reference"] in self._used_references:
                    result = VerificationResult(False, "invalid_mandate", "closed payment mandate already presented")
                else:
                    if pair_id:
                        self._used_pairs.add(pair_id)
                    self._used_references.add(result.details["reference"])
        if not result.ok:
            receipt = make_receipt(cp_key, CP_ISSUER, status="Error", reference=self._reference_or_unknown(profile_name, payment_payload), now=now, payment_id=payment_id, error=result.code, error_description=result.description)
            return {"ok": False, "payment_receipt": receipt, "error": result.code, "error_description": result.description}
        credential = sdjwt.sign_jwt(
            {"alg": "ES256", "typ": "payment-credential+jwt", "kid": self.keys.kid("credential_provider")},
            {
                "iss": CP_ISSUER,
                "iat": int(now.timestamp()),
                "exp": int(now.timestamp()) + 900,
                "credential_id": new_id("cred"),
                "profile": profile_name,
                "transaction_id": checkout_hash,
                "payment_amount": result.details["amount"],
                "payee": result.details["payee"],
                "closed_payment_mandate_reference": result.details["reference"],
                "payment_id": payment_id,
            },
            cp_key,
        )
        receipt = make_receipt(cp_key, CP_ISSUER, status="Success", reference=result.details["reference"], now=now, payment_id=payment_id)
        self.receipts[payment_id] = receipt
        return {"ok": True, "credential": credential, "payment_receipt": receipt, "payment_id": payment_id, "reference": result.details["reference"]}

    def _reference_or_unknown(self, profile_name: str, payload: Dict[str, Any]) -> str:
        try:
            if profile_name == "ap2":
                chain = payload.get("payment_mandate", "")
                from .ap2 import _split_chain

                segs = _split_chain(chain)
                if len(segs) == 2:
                    return sdjwt.sd_hash(sdjwt.serialize(segs[1][0], segs[1][1]))
            if profile_name == "vi" and isinstance(payload.get("l3a"), str):
                return sdjwt.sd_hash(payload["l3a"])
        except Exception:  # pragma: no cover
            pass
        return "unknown"


class MerchantPaymentProcessorSim:
    """Merchant-side: verify the payment credential is scoped to this checkout, then capture."""

    def __init__(self, trust: TrustStore):
        self.trust = trust
        self.captures: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def capture(self, credential: str, *, checkout_hash: str, amount_minor: int, currency: str, merchant_id: str, now: datetime) -> Dict[str, Any]:
        header, _ = sdjwt.decode_unverified(credential)
        key = self.trust.key_by_kid(header.get("kid", ""))
        if key is None or header.get("kid") != self.trust.by_role.get("credential_provider", {}).get("kid"):
            return {"ok": False, "error": "untrusted_credential_issuer"}
        try:
            _, claims = sdjwt.verify_jwt(credential, key)
        except sdjwt.SDJWTError as e:
            return {"ok": False, "error": "invalid_credential", "error_description": str(e)}
        if claims.get("transaction_id") != checkout_hash:
            return {"ok": False, "error": "credential_not_scoped_to_checkout"}
        if claims.get("payment_amount", {}).get("amount") != amount_minor or claims.get("payment_amount", {}).get("currency") != currency:
            return {"ok": False, "error": "credential_amount_mismatch"}
        if (claims.get("payee") or {}).get("id") != merchant_id:
            return {"ok": False, "error": "credential_payee_mismatch"}
        if int(claims.get("exp", 0)) < int(now.timestamp()):
            return {"ok": False, "error": "credential_expired"}
        with self._lock:
            if claims["credential_id"] in self.captures:
                return {"ok": True, **self.captures[claims["credential_id"]], "replayed": True}
            capture = {"payment_id": claims["payment_id"], "psp_confirmation_id": new_id("psp"), "amount": amount_minor, "currency": currency}
            self.captures[claims["credential_id"]] = capture
        return {"ok": True, **capture}
