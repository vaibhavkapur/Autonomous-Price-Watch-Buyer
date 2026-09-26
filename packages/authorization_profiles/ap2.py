"""AP2 v0.2 — autonomous ("Human Not Present") mandates.

Pinned to the AP2 specification v0.2 (https://ap2-protocol.org/ap2/specification/):

* Trusted Agent Provider delegation model: the Trusted Surface (deterministic
  code holding the ``agent_provider`` key) signs a *delegate SD-JWT* whose
  ``delegate_payload`` carries the open Checkout Mandate
  (``mandate.checkout.open.1``) and open Payment Mandate
  (``mandate.payment.open.1``). Both contain ``cnf.jwk`` = the Shopping Agent key.
* Constraints: ``checkout.allowed_merchants``, ``checkout.line_items``,
  ``payment.amount_range``, ``payment.allowed_payees``, ``payment.reference``,
  ``payment.execution_date``. Unknown constraints fail evaluation.
* At execution the agent closes the mandates with a key-binding JWT
  (``typ: kb+sd-jwt``) whose ``sd_hash`` covers the exact open presentation it
  extends; the closed Checkout Mandate (``mandate.checkout.1``) binds the
  merchant-signed ``checkout_jwt`` via ``checkout_hash``; the closed Payment
  Mandate (``mandate.payment.1``) carries ``transaction_id == checkout_hash``.
* Verifiers return a signed receipt with ``reference`` = hash of the closed
  presentation (computed like ``sd_hash``).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from common.clock import iso
from common.util import new_id, random_token, sha256_b64url

from . import constraints as C
from . import sdjwt
from .base import AuthorizationProfile, ConsentArtifact, ConsentRequest, PreparedPresentation, ProfileError, VerificationResult
from .sdjwt import Disclosure, element_ref

PROFILE = "ap2"
VERSION = "ap2-v0.2"
CLOCK_SKEW_S = 300

VCT_CHECKOUT_OPEN = "mandate.checkout.open.1"
VCT_CHECKOUT = "mandate.checkout.1"
VCT_PAYMENT_OPEN = "mandate.payment.open.1"
VCT_PAYMENT = "mandate.payment.1"
VCT_DELEGATE = "local.pricewatch.ap2.agent_mandate"  # agent-provider credential type carrying the mandates

AUD_MERCHANT = "merchant"
AUD_CREDENTIAL_PROVIDER = "credential-provider"


def _split_chain(chain: str) -> List[Tuple[str, List[Disclosure]]]:
    """Split ``open~d~d~kb~d~`` into [(jwt, disclosures), ...]. JWTs contain dots; disclosures do not."""
    parts = chain.split("~")
    segments: List[Tuple[str, List[Disclosure]]] = []
    cur_jwt: Optional[str] = None
    cur_discs: List[Disclosure] = []
    for p in parts:
        if not p:
            continue
        if p.count(".") == 2:
            if cur_jwt is not None:
                segments.append((cur_jwt, cur_discs))
            cur_jwt, cur_discs = p, []
        else:
            if cur_jwt is None:
                raise sdjwt.SDJWTError("disclosure before any JWT")
            cur_discs.append(Disclosure.parse(p))
    if cur_jwt is not None:
        segments.append((cur_jwt, cur_discs))
    return segments


def _time_ok(payload: Dict[str, Any], now: datetime) -> Optional[str]:
    t = int(now.timestamp())
    if "exp" in payload and t > int(payload["exp"]) + CLOCK_SKEW_S:
        return "expired"
    if "iat" in payload and int(payload["iat"]) > t + CLOCK_SKEW_S:
        return "issued in the future"
    return None


class AP2Profile(AuthorizationProfile):
    name = PROFILE
    version = VERSION

    # ------------------------------------------------------------ consent
    def issue_consent(self, req: ConsentRequest) -> ConsentArtifact:
        agent_jwk = self.trust.by_role["agent"]
        cnf = {"jwk": {k: agent_jwk[k] for k in ("kty", "crv", "x", "y")}}
        iat = int(req.issued_at.timestamp())
        exp = int(req.not_after.timestamp())

        merchant_discs = {m["id"]: Disclosure.for_element({"id": m["id"], "name": m["name"], "website": m["website"]}) for m in req.merchants}
        payee_discs = {m["id"]: Disclosure.for_element({"id": m["id"], "name": m["name"], "website": m["website"]}) for m in req.merchants}
        item_discs = {it["id"]: Disclosure.for_element({"id": it["id"], "title": it["title"]}) for it in req.acceptable_items}

        open_checkout = {
            "vct": VCT_CHECKOUT_OPEN,
            "constraints": [
                {
                    "type": "checkout.line_items",
                    "items": [{"id": "line_1", "acceptable_items": [element_ref(d) for d in item_discs.values()], "quantity": req.quantity}],
                },
                {"type": "checkout.allowed_merchants", "allowed": [element_ref(d) for d in merchant_discs.values()]},
            ],
            "cnf": cnf,
            "iat": iat,
            "exp": exp,
        }
        checkout_disc = Disclosure.for_element(open_checkout)

        open_payment = {
            "vct": VCT_PAYMENT_OPEN,
            "constraints": [
                {"type": "payment.amount_range", "currency": req.currency, "max": req.inclusive_max_minor, "min": 0},
                {"type": "payment.allowed_payees", "allowed": [element_ref(d) for d in payee_discs.values()]},
                {"type": "payment.reference", "conditional_transaction_id": checkout_disc.digest},
                {"type": "payment.execution_date", "not_after": iso(req.not_after)},
            ],
            "payment_instrument": dict(req.payment_instrument),
            "cnf": cnf,
            "iat": iat,
            "exp": exp,
        }
        payment_disc = Disclosure.for_element(open_payment)

        provider_key = self.keys.private("agent_provider")
        header = {"alg": "ES256", "typ": "dc+sd-jwt", "kid": self.keys.kid("agent_provider")}
        payload = {
            "iss": "https://trusted-surface.pricewatch.local",
            "vct": VCT_DELEGATE,
            "iat": iat,
            "exp": exp,
            "_sd_alg": sdjwt.SD_ALG,
            "delegate_payload": [element_ref(checkout_disc), element_ref(payment_disc)],
            "consent": {"watch_id": req.watch_id, "watch_version": req.watch_version, "user_id": req.user_id, "prompt_summary": req.prompt_summary},
        }
        issuer_jwt = sdjwt.sign_jwt(header, payload, provider_key)
        all_discs = [checkout_disc, payment_disc] + list(merchant_discs.values()) + list(payee_discs.values()) + list(item_discs.values())
        full = sdjwt.serialize(issuer_jwt, all_discs)
        reference = sdjwt.sd_hash(full)
        native = {
            "issuer_jwt": issuer_jwt,
            "full_presentation": full,
            "disclosures": {
                "checkout_mandate": checkout_disc.encoded,
                "payment_mandate": payment_disc.encoded,
                "merchants": {k: d.encoded for k, d in merchant_discs.items()},
                "payees": {k: d.encoded for k, d in payee_discs.items()},
                "items": {k: d.encoded for k, d in item_discs.items()},
            },
            "item_merchants": {it["id"]: it["merchant_id"] for it in req.acceptable_items},
        }
        summary = {
            "profile": PROFILE,
            "profile_version": VERSION,
            "delegation_model": "trusted_agent_provider",
            "open_checkout_mandate": {"vct": VCT_CHECKOUT_OPEN, "allowed_merchants": [m["id"] for m in req.merchants], "acceptable_items": [it["id"] for it in req.acceptable_items], "quantity": req.quantity},
            "open_payment_mandate": {"vct": VCT_PAYMENT_OPEN, "amount_range": {"currency": req.currency, "min": 0, "max": req.inclusive_max_minor}, "allowed_payees": [m["id"] for m in req.merchants], "execution_not_after": iso(req.not_after)},
            "agent_key_id": agent_jwk["kid"],
            "expires_at": iso(req.not_after),
        }
        return ConsentArtifact(
            profile=PROFILE, profile_version=VERSION, reference=reference, consent_reference=new_id("consent"),
            expires_at=req.not_after, agent_key_id=agent_jwk["kid"], native=native, summary=summary,
        )

    # -------------------------------------------------------------- agent
    def prepare(self, consent: Dict[str, Any], checkout_jwt: str, checkout: Dict[str, Any], merchant: Dict[str, str], amount_minor: int, currency: str, now: datetime, audience_merchant: str) -> PreparedPresentation:
        d = consent["disclosures"]
        issuer_jwt = consent["issuer_jwt"]
        checkout_hash = sha256_b64url(checkout_jwt.encode("ascii"))
        sku = checkout["line_items"][0]["item"]["id"]
        if sku not in d["items"]:
            raise ProfileError("invalid_mandate", "checkout item %s is not among the acceptable items" % sku)
        if merchant["id"] not in d["merchants"]:
            raise ProfileError("invalid_mandate", "merchant %s not in the open mandate" % merchant["id"])
        agent_key = self.keys.private("agent")
        iat = int(now.timestamp())

        # -- closed checkout mandate, presented to the merchant (minimal disclosure)
        open_m = sdjwt.serialize(issuer_jwt, [Disclosure.parse(d["checkout_mandate"]), Disclosure.parse(d["merchants"][merchant["id"]]), Disclosure.parse(d["items"][sku])])
        cj_disc = Disclosure.for_property("checkout_jwt", checkout_jwt)
        closed_checkout = {"_sd": [cj_disc.digest], "vct": VCT_CHECKOUT, "checkout_hash": checkout_hash, "iat": iat, "exp": iat + 300}
        closed_disc = Disclosure.for_element(closed_checkout)
        kb_payload = {"delegate_payload": [element_ref(closed_disc)], "iat": iat, "aud": AUD_MERCHANT, "nonce": random_token(16), "sd_hash": sdjwt.sd_hash(open_m), "_sd_alg": sdjwt.SD_ALG}
        kb_jwt = sdjwt.sign_jwt({"alg": "ES256", "typ": "kb+sd-jwt", "kid": self.keys.kid("agent")}, kb_payload, agent_key)
        closed_m = sdjwt.serialize(kb_jwt, [closed_disc, cj_disc])
        checkout_chain = open_m + closed_m

        # -- closed payment mandate, presented to the credential provider / MPP
        open_p = sdjwt.serialize(issuer_jwt, [Disclosure.parse(d["payment_mandate"]), Disclosure.parse(d["payees"][merchant["id"]])])
        open_payment = Disclosure.parse(d["payment_mandate"]).value
        closed_payment = {
            "vct": VCT_PAYMENT,
            "transaction_id": checkout_hash,
            "payee": {"id": merchant["id"], "name": merchant["name"], "website": merchant["website"]},
            "payment_amount": {"amount": int(amount_minor), "currency": currency},
            "payment_instrument": dict(open_payment["payment_instrument"]),
            "execution_date": iso(now),
            "iat": iat,
            "exp": iat + 300,
        }
        closed_p_disc = Disclosure.for_element(closed_payment)
        kb_p_payload = {"delegate_payload": [element_ref(closed_p_disc)], "iat": iat, "aud": AUD_CREDENTIAL_PROVIDER, "nonce": random_token(16), "sd_hash": sdjwt.sd_hash(open_p), "_sd_alg": sdjwt.SD_ALG}
        kb_p_jwt = sdjwt.sign_jwt({"alg": "ES256", "typ": "kb+sd-jwt", "kid": self.keys.kid("agent")}, kb_p_payload, agent_key)
        closed_p = sdjwt.serialize(kb_p_jwt, [closed_p_disc])
        payment_chain = open_p + closed_p

        return PreparedPresentation(
            profile=PROFILE,
            reference=sdjwt.sd_hash(closed_m),
            checkout_hash=checkout_hash,
            merchant_payload={"checkout_mandate": checkout_chain},
            payment_payload={"payment_mandate": payment_chain, "checkout_mandate": checkout_chain},
            native={"checkout_mandate_chain": checkout_chain, "payment_mandate_chain": payment_chain, "checkout_hash": checkout_hash},
        )

    # ---------------------------------------------------------- verifiers
    def _verify_open(self, jwt_str: str, discs: List[Disclosure], now: datetime, want_vct: str) -> Tuple[Dict[str, Any], Dict[str, Any], List[Dict[str, Any]]]:
        header, _ = sdjwt.decode_unverified(jwt_str)
        key = self.trust.key_by_kid(header.get("kid", "")) if header.get("kid") else None
        trusted_kid = self.trust.by_role.get("agent_provider", {}).get("kid")
        if key is None or header.get("kid") != trusted_kid:
            raise ProfileError("invalid_credential", "open mandate not signed by a trusted agent provider key")
        _, payload = sdjwt.verify_jwt(jwt_str, key)
        sdjwt.check_sd_alg(payload)
        why = _time_ok(payload, now)
        if why:
            raise ProfileError("invalid_credential", "open mandate %s" % why)
        resolved = sdjwt.resolve_strict(payload, discs)
        mandates = [m for m in resolved.get("delegate_payload", []) if isinstance(m, dict) and "vct" in m]
        wanted = [m for m in mandates if m.get("vct") == want_vct]
        if len(wanted) != 1:
            raise ProfileError("invalid_mandate", "expected exactly one %s, got %d" % (want_vct, len(wanted)))
        open_mandate = wanted[0]
        why = _time_ok(open_mandate, now)
        if why:
            raise ProfileError("invalid_credential", "open %s %s" % (want_vct, why))
        if "cnf" not in open_mandate or "jwk" not in open_mandate["cnf"]:
            raise ProfileError("invalid_credential", "open mandate lacks cnf.jwk")
        return open_mandate, resolved, mandates

    def _verify_closed(self, kb_jwt: str, discs: List[Disclosure], open_presentation: str, cnf_jwk: Dict[str, Any], aud: str, now: datetime, want_vct: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        from joserfc.jwk import ECKey

        header, _ = sdjwt.decode_unverified(kb_jwt)
        if header.get("typ") != "kb+sd-jwt":
            raise ProfileError("invalid_credential", "closed mandate typ must be kb+sd-jwt")
        _, payload = sdjwt.verify_jwt(kb_jwt, ECKey.import_key(cnf_jwk))
        sdjwt.check_sd_alg(payload)
        if payload.get("sd_hash") != sdjwt.sd_hash(open_presentation):
            raise ProfileError("invalid_credential", "sd_hash does not bind the presented open mandate")
        if payload.get("aud") != aud:
            raise ProfileError("invalid_credential", "closed mandate audience %r != %r" % (payload.get("aud"), aud))
        if not payload.get("nonce"):
            raise ProfileError("invalid_credential", "closed mandate lacks nonce")
        why = _time_ok(payload, now)
        if why:
            raise ProfileError("invalid_credential", "closed mandate %s" % why)
        resolved = sdjwt.resolve_strict(payload, discs)
        closed = [m for m in resolved.get("delegate_payload", []) if isinstance(m, dict) and m.get("vct") == want_vct]
        if len(closed) != 1:
            raise ProfileError("invalid_mandate", "expected exactly one closed %s" % want_vct)
        why = _time_ok(closed[0], now)
        if why:
            raise ProfileError("invalid_credential", "closed %s %s" % (want_vct, why))
        return closed[0], payload

    def verify_for_merchant(self, merchant_payload: Dict[str, Any], checkout_jwt: str, merchant: Dict[str, str], now: datetime) -> VerificationResult:
        try:
            chain = merchant_payload.get("checkout_mandate")
            if not isinstance(chain, str):
                raise ProfileError("invalid_credential", "missing checkout_mandate")
            segs = _split_chain(chain)
            if len(segs) != 2:
                raise ProfileError("invalid_credential", "expected open + closed mandate chain")
            (open_jwt, open_discs), (kb_jwt, closed_discs) = segs
            open_pres = sdjwt.serialize(open_jwt, open_discs)
            open_mandate, _, _ = self._verify_open(open_jwt, open_discs, now, VCT_CHECKOUT_OPEN)
            closed, _ = self._verify_closed(kb_jwt, closed_discs, open_pres, open_mandate["cnf"]["jwk"], AUD_MERCHANT, now, VCT_CHECKOUT)
            disclosed_jwt = closed.get("checkout_jwt")
            if disclosed_jwt != checkout_jwt:
                raise ProfileError("invalid_mandate", "checkout_jwt in mandate is not the checkout this merchant issued")
            if closed.get("checkout_hash") != sha256_b64url(checkout_jwt.encode("ascii")):
                raise ProfileError("invalid_mandate", "checkout_hash does not match checkout_jwt")
            _, checkout = sdjwt.decode_unverified(checkout_jwt)
            notes = []
            for c in open_mandate.get("constraints", []):
                t = c.get("type")
                if t == "checkout.allowed_merchants":
                    ok, why = C.eval_allowed_merchants(c.get("allowed", []), merchant)
                elif t == "checkout.line_items":
                    ok, why = C.eval_line_items(c.get("items", []), checkout.get("line_items", []), exact=True)
                else:
                    raise ProfileError("unresolved_constraint", "unknown constraint type %r" % t)
                notes.append(why)
                if not ok:
                    raise ProfileError("invalid_mandate", why)
            return VerificationResult(True, details={"reference": sdjwt.sd_hash(sdjwt.serialize(kb_jwt, closed_discs)), "checkout_hash": closed["checkout_hash"], "constraints": notes})
        except ProfileError as e:
            return VerificationResult(False, e.code, e.description)
        except sdjwt.SDJWTError as e:
            return VerificationResult(False, "invalid_credential", str(e))

    def verify_for_payment(self, payment_payload: Dict[str, Any], checkout_hash: str, now: datetime) -> VerificationResult:
        try:
            chain = payment_payload.get("payment_mandate")
            if not isinstance(chain, str):
                raise ProfileError("invalid_credential", "missing payment_mandate")
            segs = _split_chain(chain)
            if len(segs) != 2:
                raise ProfileError("invalid_credential", "expected open + closed payment mandate chain")
            (open_jwt, open_discs), (kb_jwt, closed_discs) = segs
            open_pres = sdjwt.serialize(open_jwt, open_discs)
            open_mandate, _, _ = self._verify_open(open_jwt, open_discs, now, VCT_PAYMENT_OPEN)
            closed, _ = self._verify_closed(kb_jwt, closed_discs, open_pres, open_mandate["cnf"]["jwk"], AUD_CREDENTIAL_PROVIDER, now, VCT_PAYMENT)
            for f in ("transaction_id", "payee", "payment_amount", "payment_instrument"):
                if f not in closed:
                    raise ProfileError("invalid_mandate", "closed payment mandate lacks %s" % f)
            if closed["transaction_id"] != checkout_hash:
                raise ProfileError("invalid_mandate", "transaction_id does not reference this checkout")
            # Rule 2: claims fixed in the open mandate must be unchanged in the closed one.
            for k, v in open_mandate.items():
                if k in ("vct", "constraints", "cnf", "iat", "exp"):
                    continue
                if closed.get(k) != v:
                    raise ProfileError("invalid_mandate", "closed mandate changed open claim %r" % k)
            # Reference constraint needs the checkout mandate chain to inspect the delegate chain.
            checkout_chain = payment_payload.get("checkout_mandate")
            open_checkout_digest = None
            if isinstance(checkout_chain, str):
                cs = _split_chain(checkout_chain)
                if cs:
                    open_checkout_digest = next((d.digest for d in cs[0][1] if isinstance(d.value, dict) and d.value.get("vct") == VCT_CHECKOUT_OPEN), None)
            notes = []
            for c in open_mandate.get("constraints", []):
                t = c.get("type")
                if t == "payment.amount_range":
                    ok, why = C.eval_amount_range(c, closed["payment_amount"])
                elif t == "payment.allowed_payees":
                    ok, why = C.eval_allowed_payees(c.get("allowed", []), closed["payee"])
                elif t == "payment.reference":
                    ok = open_checkout_digest is not None and c.get("conditional_transaction_id") == open_checkout_digest
                    why = "reference: bound to open checkout mandate" if ok else "reference: conditional_transaction_id does not match the open checkout mandate in the presented chain"
                elif t == "payment.execution_date":
                    ok, why = C.eval_execution_date(c, closed.get("execution_date"), now)
                else:
                    raise ProfileError("unresolved_constraint", "unknown constraint type %r" % t)
                notes.append(why)
                if not ok:
                    raise ProfileError("invalid_mandate", why)
            return VerificationResult(True, details={"reference": sdjwt.sd_hash(sdjwt.serialize(kb_jwt, closed_discs)), "amount": closed["payment_amount"], "payee": closed["payee"], "constraints": notes})
        except ProfileError as e:
            return VerificationResult(False, e.code, e.description)
        except sdjwt.SDJWTError as e:
            return VerificationResult(False, "invalid_credential", str(e))


def make_receipt(key, issuer: str, *, status: str, reference: str, now: datetime, order_id: Optional[str] = None, payment_id: Optional[str] = None, error: Optional[str] = None, error_description: Optional[str] = None) -> str:
    """Checkout / Payment Receipt JWT (AP2 receipt schema)."""
    payload: Dict[str, Any] = {"iss": issuer, "iat": int(now.timestamp()), "status": status, "reference": reference}
    if status == "Success":
        if order_id:
            payload["order_id"] = order_id
        if payment_id:
            payload["payment_id"] = payment_id
    else:
        payload["error"] = error or "invalid_mandate"
        payload["error_description"] = error_description or ""
        if payment_id:
            payload["payment_id"] = payment_id
    return sdjwt.sign_jwt({"alg": "ES256", "typ": "JWT", "kid": key.as_dict(private=False).get("kid")}, payload, key)
