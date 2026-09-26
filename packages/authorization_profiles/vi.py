"""Verifiable Intent v0.1-draft — autonomous (3-layer) mode.

Pinned to https://github.com/agent-intent/verifiable-intent (spec/credential-format.md,
spec/constraints.md, version 0.1-draft, 2026-02-18).

* L1  ``typ: sd+jwt``        issuer → user: identity + ``cnf.jwk`` (user device key)
* L2  ``typ: kb-sd-jwt+kb``  user → agent: ``sd_hash`` over the serialized L1, two
      mandate disclosures (``mandate.checkout.open.1`` / ``mandate.payment.open.1``)
      each carrying ``cnf.jwk`` (agent key incl. ``kid``) and constraints; the
      payment mandate carries ``mandate.payment.reference`` whose
      ``conditional_transaction_id`` is the checkout mandate's disclosure digest.
* L3a/L3b ``typ: kb-sd-jwt`` agent → network / merchant: final values, selective
      ``sd_hash`` over the recipient-specific L2 presentation (§5.4), header
      ``kid`` resolved against L2 ``cnf.jwk.kid``; no ``cnf``; ``L3a.transaction_id
      == L3b.checkout_hash``.

Constraint vocabulary: ``mandate.checkout.allowed_merchants``,
``mandate.checkout.line_items``, ``mandate.payment.amount_range``,
``mandate.payment.allowed_payees``, ``mandate.payment.reference``.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from joserfc.jwk import ECKey

from common.clock import iso
from common.util import new_id, random_token, sha256_b64url

from . import constraints as C
from . import sdjwt
from .base import AuthorizationProfile, ConsentArtifact, ConsentRequest, PreparedPresentation, ProfileError, VerificationResult
from .sdjwt import Disclosure, element_ref

PROFILE = "vi"
VERSION = "vi-v0.1-draft"
CLOCK_SKEW_S = 300

L1_VCT = "https://fixtures.pricewatch.local/credentials/card"  # fixture credential profile (not the Mastercard reference profile)
VCT_CHECKOUT_OPEN = "mandate.checkout.open.1"
VCT_CHECKOUT = "mandate.checkout.1"
VCT_PAYMENT_OPEN = "mandate.payment.open.1"
VCT_PAYMENT = "mandate.payment.1"
KNOWN_VCTS = {L1_VCT, VCT_CHECKOUT_OPEN, VCT_CHECKOUT, VCT_PAYMENT_OPEN, VCT_PAYMENT}

ISSUER = "https://issuer.fixture.pricewatch.local"
WALLET = "https://wallet.fixture.pricewatch.local"
NETWORK_AUD = "https://network.fixture.pricewatch.local/vi/authorize"


def _time_ok(payload: Dict[str, Any], now: datetime) -> Optional[str]:
    t = int(now.timestamp())
    if "exp" in payload and t > int(payload["exp"]) + CLOCK_SKEW_S:
        return "expired"
    if "iat" in payload and int(payload["iat"]) > t + CLOCK_SKEW_S:
        return "iat in the future"
    return None


class VIProfile(AuthorizationProfile):
    name = PROFILE
    version = VERSION

    # ------------------------------------------------------------ Layer 1
    def issue_layer1(self, user_id: str, now: datetime) -> str:
        """Credential-provider fixture: issue the root credential for the user."""
        issuer_key = self.keys.private("vi_issuer")
        user_jwk = self.trust.by_role["user_device"]
        email_disc = Disclosure.for_property("email", "%s@example.invalid" % user_id)
        payload = {
            "iss": ISSUER,
            "sub": user_id,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(days=365)).timestamp()),
            "vct": L1_VCT,
            "cnf": {"jwk": {k: user_jwk[k] for k in ("kty", "crv", "x", "y")}},
            "pan_last_four": "4242",
            "scheme": "fixture",
            "card_id": "card-fixture-%s" % user_id,
            "_sd_alg": sdjwt.SD_ALG,
            "_sd": [email_disc.digest],
        }
        jwt_str = sdjwt.sign_jwt({"alg": "ES256", "typ": "sd+jwt", "kid": self.keys.kid("vi_issuer")}, payload, issuer_key)
        # The wallet presents L1 *without* the email disclosure for delegation; sd_hash binds that form.
        return sdjwt.serialize(jwt_str, [])

    # ------------------------------------------------------------ consent
    def issue_consent(self, req: ConsentRequest) -> ConsentArtifact:
        l1 = self.issue_layer1(req.user_id, req.issued_at)
        agent_jwk = self.trust.by_role["agent"]
        cnf = {"jwk": {k: agent_jwk[k] for k in ("kty", "crv", "x", "y", "kid")}}

        merchant_discs = {m["id"]: Disclosure.for_element({"id": m["id"], "name": m["name"], "website": m["website"]}) for m in req.merchants}
        payee_discs = {m["id"]: Disclosure.for_element({"id": m["id"], "name": m["name"], "website": m["website"]}) for m in req.merchants}
        item_discs = {it["id"]: Disclosure.for_element({"id": it["id"], "title": it["title"]}) for it in req.acceptable_items}

        checkout_mandate = {
            "vct": VCT_CHECKOUT_OPEN,
            "cnf": cnf,
            "constraints": [
                {"type": "mandate.checkout.allowed_merchants", "allowed": [element_ref(d) for d in merchant_discs.values()]},
                {"type": "mandate.checkout.line_items", "match_mode": "exact", "items": [{"id": "line-1", "acceptable_items": [element_ref(d) for d in item_discs.values()], "quantity": req.quantity}]},
            ],
            "prompt_summary": req.prompt_summary,
        }
        checkout_disc = Disclosure.for_element(checkout_mandate)
        payment_mandate = {
            "vct": VCT_PAYMENT_OPEN,
            "cnf": cnf,
            "payment_instrument": dict(req.payment_instrument),
            "constraints": [
                {"type": "mandate.payment.amount_range", "currency": req.currency, "min": 0, "max": req.inclusive_max_minor},
                {"type": "mandate.payment.allowed_payees", "allowed": [element_ref(d) for d in payee_discs.values()]},
                {"type": "mandate.payment.reference", "conditional_transaction_id": checkout_disc.digest},
            ],
        }
        payment_disc = Disclosure.for_element(payment_mandate)
        context_disc = Disclosure.for_property("consent_context", {"watch_id": req.watch_id, "watch_version": req.watch_version, "not_after": iso(req.not_after)})

        l2_nonce = random_token(16)
        l2_payload = {
            "nonce": l2_nonce,
            "aud": req.agent_id if req.agent_id.startswith("http") else "https://agent.pricewatch.local/agents/%s" % req.agent_id,
            "iss": WALLET,
            "iat": int(req.issued_at.timestamp()),
            "exp": int(req.not_after.timestamp()),
            "sd_hash": sdjwt.sd_hash(l1),
            "_sd_alg": sdjwt.SD_ALG,
            "_sd": [context_disc.digest],
            "delegate_payload": [element_ref(checkout_disc), element_ref(payment_disc)],
        }
        l2_jwt = sdjwt.sign_jwt({"alg": "ES256", "typ": "kb-sd-jwt+kb", "kid": self.keys.kid("user_device")}, l2_payload, self.keys.private("user_device"))
        all_discs = [checkout_disc, payment_disc, context_disc] + list(merchant_discs.values()) + list(payee_discs.values()) + list(item_discs.values())
        l2_full = sdjwt.serialize(l2_jwt, all_discs)
        native = {
            "l1": l1,
            "l2_jwt": l2_jwt,
            "l2_full": l2_full,
            "l2_nonce": l2_nonce,
            "disclosures": {
                "checkout_mandate": checkout_disc.encoded,
                "payment_mandate": payment_disc.encoded,
                "consent_context": context_disc.encoded,
                "merchants": {k: d.encoded for k, d in merchant_discs.items()},
                "payees": {k: d.encoded for k, d in payee_discs.items()},
                "items": {k: d.encoded for k, d in item_discs.items()},
            },
            "item_titles": {it["id"]: it["title"] for it in req.acceptable_items},
        }
        summary = {
            "profile": PROFILE,
            "profile_version": VERSION,
            "layers": {"l1": {"typ": "sd+jwt", "vct": L1_VCT, "issuer": ISSUER}, "l2": {"typ": "kb-sd-jwt+kb", "aud": l2_payload["aud"], "exp": iso(req.not_after)}, "l3": {"typ": "kb-sd-jwt", "lifetime_s": 300}},
            "checkout_mandate": {"vct": VCT_CHECKOUT_OPEN, "allowed_merchants": [m["id"] for m in req.merchants], "acceptable_items": [it["id"] for it in req.acceptable_items], "quantity": req.quantity, "match_mode": "exact"},
            "payment_mandate": {"vct": VCT_PAYMENT_OPEN, "amount_range": {"currency": req.currency, "min": 0, "max": req.inclusive_max_minor}, "allowed_payees": [m["id"] for m in req.merchants], "reference": checkout_disc.digest},
            "agent_key_id": agent_jwk["kid"],
            "expires_at": iso(req.not_after),
        }
        return ConsentArtifact(PROFILE, VERSION, sdjwt.sd_hash(l2_full), new_id("consent"), req.not_after, agent_jwk["kid"], native, summary)

    # -------------------------------------------------------------- agent
    def prepare(self, consent: Dict[str, Any], checkout_jwt: str, checkout: Dict[str, Any], merchant: Dict[str, str], amount_minor: int, currency: str, now: datetime, audience_merchant: str) -> PreparedPresentation:
        d = consent["disclosures"]
        l2_jwt = consent["l2_jwt"]
        checkout_hash = sha256_b64url(checkout_jwt.encode("ascii"))
        li = checkout["line_items"][0]
        sku = li["item"]["id"]
        if sku not in d["items"] or merchant["id"] not in d["merchants"]:
            raise ProfileError("invalid_mandate", "checkout is outside the delegated item/merchant scope")
        agent_key = self.keys.private("agent")
        kid = self.keys.kid("agent")
        iat = int(now.timestamp())
        exp = iat + 300

        # L3b → merchant
        l2_view_b = sdjwt.serialize(l2_jwt, [Disclosure.parse(d["checkout_mandate"]), Disclosure.parse(d["merchants"][merchant["id"]]), Disclosure.parse(d["items"][sku])])
        final_checkout = {
            "vct": VCT_CHECKOUT,
            "checkout_jwt": checkout_jwt,
            "checkout_hash": checkout_hash,
            "line_items": [{"id": sku, "title": li["item"].get("title"), "quantity": int(li["quantity"]), "unit_price": int(li["item"]["price"]), "currency": currency}],
        }
        fc_disc = Disclosure.for_element(final_checkout)
        l3b_payload = {"nonce": random_token(16), "aud": audience_merchant, "iat": iat, "exp": exp, "sd_hash": sdjwt.sd_hash(l2_view_b), "_sd_alg": sdjwt.SD_ALG, "_sd": [], "delegate_payload": [element_ref(fc_disc)]}
        l3b = sdjwt.serialize(sdjwt.sign_jwt({"alg": "ES256", "typ": "kb-sd-jwt", "kid": kid}, l3b_payload, agent_key), [fc_disc])

        # L3a → payment network
        l2_view_a = sdjwt.serialize(l2_jwt, [Disclosure.parse(d["payment_mandate"]), Disclosure.parse(d["payees"][merchant["id"]])])
        open_payment = Disclosure.parse(d["payment_mandate"]).value
        final_payment = {
            "vct": VCT_PAYMENT,
            "payment_instrument": dict(open_payment["payment_instrument"]),
            "payment_amount": {"currency": currency, "amount": int(amount_minor)},
            "payee": {"id": merchant["id"], "name": merchant["name"], "website": merchant["website"]},
            "transaction_id": checkout_hash,
            "selected_merchant": {"id": merchant["id"], "name": merchant["name"], "website": merchant["website"]},
        }
        fp_disc = Disclosure.for_element(final_payment)
        l3a_payload = {"nonce": random_token(16), "aud": NETWORK_AUD, "iat": iat, "exp": exp, "sd_hash": sdjwt.sd_hash(l2_view_a), "_sd_alg": sdjwt.SD_ALG, "_sd": [], "delegate_payload": [element_ref(fp_disc)]}
        l3a = sdjwt.serialize(sdjwt.sign_jwt({"alg": "ES256", "typ": "kb-sd-jwt", "kid": kid}, l3a_payload, agent_key), [fp_disc])

        merchant_payload = {"vi": {"l1": consent["l1"], "l2": l2_view_b, "l3b": l3b}}
        payment_payload = {"l1": consent["l1"], "l2": l2_view_a, "l3a": l3a, "l3b": l3b}
        return PreparedPresentation(PROFILE, sdjwt.sd_hash(l3b), checkout_hash, merchant_payload, payment_payload, {"l1": consent["l1"], "l2_checkout_view": l2_view_b, "l2_payment_view": l2_view_a, "l3a": l3a, "l3b": l3b, "checkout_hash": checkout_hash})

    # ---------------------------------------------------------- verifiers
    def _verify_l1(self, l1: str, now: datetime) -> Dict[str, Any]:
        jwt_str, discs, kb = sdjwt.parse(l1)
        if kb:
            raise ProfileError("invalid_credential", "L1 must not carry a key-binding JWT")
        header, _ = sdjwt.decode_unverified(jwt_str)
        if header.get("typ") != "sd+jwt":
            raise ProfileError("invalid_credential", "L1 typ must be sd+jwt")
        key = self.trust.key_by_kid(header.get("kid", ""))
        if key is None or header.get("kid") != self.trust.by_role.get("vi_issuer", {}).get("kid"):
            raise ProfileError("invalid_credential", "L1 kid not resolvable against the trusted issuer JWKS")
        _, payload = sdjwt.verify_jwt(jwt_str, key)
        if payload.get("vct") not in KNOWN_VCTS:
            raise ProfileError("invalid_credential", "unrecognised L1 vct")
        if "sd_hash" in payload:
            raise ProfileError("invalid_credential", "L1 must not contain sd_hash")
        why = _time_ok(payload, now)
        if why:
            raise ProfileError("invalid_credential", "L1 %s" % why)
        sdjwt.check_sd_alg(payload)
        if "cnf" not in payload or "jwk" not in payload["cnf"]:
            raise ProfileError("invalid_credential", "L1 lacks cnf.jwk")
        sdjwt.resolve_strict(payload, discs)
        return payload

    def _verify_l2(self, l2: str, l1_payload: Dict[str, Any], l1_serialized: str, now: datetime) -> Tuple[Dict[str, Any], List[Dict[str, Any]], str]:
        jwt_str, discs, kb = sdjwt.parse(l2)
        if kb:
            raise ProfileError("invalid_credential", "L2 presentation must not carry a trailing KB-JWT")
        header, _ = sdjwt.decode_unverified(jwt_str)
        if header.get("typ") != "kb-sd-jwt+kb":
            raise ProfileError("invalid_credential", "L2 autonomous typ must be kb-sd-jwt+kb")
        _, payload = sdjwt.verify_jwt(jwt_str, ECKey.import_key(l1_payload["cnf"]["jwk"]))
        if payload.get("sd_hash") != sdjwt.sd_hash(l1_serialized):
            raise ProfileError("invalid_credential", "L2 sd_hash does not bind the presented L1")
        why = _time_ok(payload, now)
        if why:
            raise ProfileError("invalid_credential", "L2 %s" % why)
        for f in ("nonce", "aud", "iat", "exp", "delegate_payload", "_sd"):
            if f not in payload:
                raise ProfileError("invalid_credential", "L2 lacks %s" % f)
        sdjwt.check_sd_alg(payload)
        if len(payload["delegate_payload"]) < 2:
            raise ProfileError("invalid_credential", "L2 must reference a checkout/payment mandate pair")
        resolved = sdjwt.resolve_strict(payload, discs)
        mandates = [m for m in resolved.get("delegate_payload", []) if isinstance(m, dict)]
        for m in mandates:
            if m.get("vct") not in (VCT_CHECKOUT_OPEN, VCT_PAYMENT_OPEN):
                raise ProfileError("invalid_credential", "unexpected L2 mandate vct %r" % m.get("vct"))
            if "cnf" not in m or "jwk" not in m["cnf"] or "kid" not in m["cnf"]["jwk"]:
                raise ProfileError("invalid_credential", "autonomous L2 mandate lacks cnf.jwk.kid")
            if not m.get("constraints"):
                raise ProfileError("invalid_credential", "autonomous L2 mandate lacks constraints")
        cnfs = {json.dumps(m["cnf"]["jwk"], sort_keys=True) for m in mandates}
        if len(cnfs) > 1:
            raise ProfileError("invalid_credential", "L2 mandates bind different agent keys")
        # pairing (only checkable when both are disclosed)
        checkouts = [m for m in mandates if m["vct"] == VCT_CHECKOUT_OPEN]
        payments = [m for m in mandates if m["vct"] == VCT_PAYMENT_OPEN]
        if checkouts and payments:
            ck_digest = next(d.digest for d in discs if isinstance(d.value, dict) and d.value.get("vct") == VCT_CHECKOUT_OPEN)
            refs = [c for p in payments for c in p["constraints"] if c.get("type") == "mandate.payment.reference"]
            if not refs or refs[0].get("conditional_transaction_id") != ck_digest:
                raise ProfileError("invalid_credential", "L2 mandate pair is orphaned")
        return payload, mandates, payload["nonce"]

    def _verify_l3(self, l3: str, l2_presentation: str, l2_nonce: str, agent_cnf: Dict[str, Any], aud: str, now: datetime, want_vct: str) -> Dict[str, Any]:
        jwt_str, discs, kb = sdjwt.parse(l3)
        header, _ = sdjwt.decode_unverified(jwt_str)
        if header.get("typ") != "kb-sd-jwt":
            raise ProfileError("invalid_credential", "L3 typ must be kb-sd-jwt")
        if "jwk" in header:
            raise ProfileError("invalid_credential", "L3 header must not self-assert a jwk")
        if header.get("kid") != agent_cnf.get("kid"):
            raise ProfileError("invalid_credential", "L3 kid does not match L2 cnf.jwk.kid")
        _, payload = sdjwt.verify_jwt(jwt_str, ECKey.import_key({k: agent_cnf[k] for k in ("kty", "crv", "x", "y")}))
        if "cnf" in payload:
            raise ProfileError("invalid_credential", "L3 must not contain cnf")
        if payload.get("sd_hash") != sdjwt.sd_hash(l2_presentation):
            raise ProfileError("invalid_credential", "L3 sd_hash does not bind the presented L2 view")
        if payload.get("aud") != aud:
            raise ProfileError("invalid_credential", "L3 aud %r != %r" % (payload.get("aud"), aud))
        if not payload.get("nonce") or payload["nonce"] == l2_nonce:
            raise ProfileError("invalid_credential", "L3 nonce missing or equal to L2 nonce")
        why = _time_ok(payload, now)
        if why:
            raise ProfileError("invalid_credential", "L3 %s" % why)
        if int(payload["exp"]) - int(payload["iat"]) > 3600:
            raise ProfileError("invalid_credential", "L3 lifetime exceeds one hour")
        sdjwt.check_sd_alg(payload)
        resolved = sdjwt.resolve_strict(payload, discs)
        finals = [m for m in resolved.get("delegate_payload", []) if isinstance(m, dict) and m.get("vct") == want_vct]
        if len(finals) != 1:
            raise ProfileError("invalid_credential", "expected exactly one final %s" % want_vct)
        final = finals[0]
        if "cnf" in final or "constraints" in final:
            raise ProfileError("invalid_credential", "final mandate must not contain cnf/constraints")
        return final

    def verify_for_merchant(self, merchant_payload: Dict[str, Any], checkout_jwt: str, merchant: Dict[str, str], now: datetime) -> VerificationResult:
        try:
            vi = merchant_payload.get("vi") or {}
            for f in ("l1", "l2", "l3b"):
                if not isinstance(vi.get(f), str):
                    raise ProfileError("invalid_credential", "missing %s" % f)
            l1_payload = self._verify_l1(vi["l1"], now)
            _, mandates, l2_nonce = self._verify_l2(vi["l2"], l1_payload, vi["l1"], now)
            ck = [m for m in mandates if m["vct"] == VCT_CHECKOUT_OPEN]
            if len(ck) != 1:
                raise ProfileError("invalid_credential", "merchant view must disclose exactly one checkout mandate")
            ck = ck[0]
            final = self._verify_l3(vi["l3b"], vi["l2"], l2_nonce, ck["cnf"]["jwk"], merchant["website"], now, VCT_CHECKOUT)
            if final.get("checkout_jwt") != checkout_jwt:
                raise ProfileError("invalid_mandate", "checkout_jwt is not the checkout this merchant issued")
            if final.get("checkout_hash") != sha256_b64url(checkout_jwt.encode("ascii")):
                raise ProfileError("invalid_mandate", "checkout_hash mismatch")
            _, checkout = sdjwt.decode_unverified(checkout_jwt)
            # fulfillment line items must equal the merchant's own checkout (application check, §9 plan)
            cart = {(li["item"]["id"], int(li["quantity"])) for li in checkout.get("line_items", [])}
            fulfil = {(li.get("id") or li.get("sku"), int(li["quantity"])) for li in final.get("line_items", [])}
            if cart != fulfil:
                raise ProfileError("invalid_mandate", "L3b line_items differ from the signed checkout")
            notes = []
            for c in ck["constraints"]:
                t = c.get("type")
                if t == "mandate.checkout.allowed_merchants":
                    ok, why = C.eval_allowed_merchants(c.get("allowed", []), {"id": (checkout.get("merchant") or {}).get("id"), "name": (checkout.get("merchant") or {}).get("name"), "website": (checkout.get("merchant") or {}).get("website")})
                elif t == "mandate.checkout.line_items":
                    mode = c.get("match_mode", "minimum")
                    if mode not in ("minimum", "exact"):
                        raise ProfileError("invalid_mandate", "unrecognised match_mode")
                    ok, why = C.eval_line_items(c.get("items", []), final.get("line_items", []), exact=(mode == "exact"))
                else:
                    raise ProfileError("unresolved_constraint", "unknown constraint type %r" % t)
                notes.append(why)
                if not ok:
                    raise ProfileError("invalid_mandate", why)
            return VerificationResult(True, details={"reference": sdjwt.sd_hash(vi["l3b"]), "checkout_hash": final["checkout_hash"], "constraints": notes})
        except ProfileError as e:
            return VerificationResult(False, e.code, e.description)
        except (sdjwt.SDJWTError, KeyError, TypeError, ValueError) as e:
            return VerificationResult(False, "invalid_credential", "%s: %s" % (e.__class__.__name__, e))

    def verify_for_payment(self, payment_payload: Dict[str, Any], checkout_hash: str, now: datetime) -> VerificationResult:
        try:
            for f in ("l1", "l2", "l3a"):
                if not isinstance(payment_payload.get(f), str):
                    raise ProfileError("invalid_credential", "missing %s" % f)
            l1_payload = self._verify_l1(payment_payload["l1"], now)
            l2_payload, mandates, l2_nonce = self._verify_l2(payment_payload["l2"], l1_payload, payment_payload["l1"], now)
            pm = [m for m in mandates if m["vct"] == VCT_PAYMENT_OPEN]
            if len(pm) != 1:
                raise ProfileError("invalid_credential", "network view must disclose exactly one payment mandate")
            pm = pm[0]
            final = self._verify_l3(payment_payload["l3a"], payment_payload["l2"], l2_nonce, pm["cnf"]["jwk"], NETWORK_AUD, now, VCT_PAYMENT)
            for f in ("payment_instrument", "payment_amount", "payee", "transaction_id"):
                if f not in final:
                    raise ProfileError("invalid_mandate", "final payment mandate lacks %s" % f)
            if final["transaction_id"] != checkout_hash:
                raise ProfileError("invalid_mandate", "transaction_id does not reference this checkout")
            if final["payment_instrument"] != pm["payment_instrument"]:
                raise ProfileError("invalid_mandate", "payment_instrument changed between L2 and L3a")
            l2_refs = {r.get("...") for r in l2_payload.get("delegate_payload", []) if isinstance(r, dict)}
            notes = []
            for c in pm["constraints"]:
                t = c.get("type")
                if t == "mandate.payment.amount_range":
                    ok, why = C.eval_amount_range(c, final["payment_amount"])
                elif t == "mandate.payment.allowed_payees":
                    ok, why = C.eval_allowed_payees(c.get("allowed", []), final["payee"])
                elif t == "mandate.payment.reference":
                    ok = c.get("conditional_transaction_id") in l2_refs
                    why = "reference: pairs with a checkout mandate in this L2" if ok else "reference: conditional_transaction_id not found in L2 delegate_payload"
                else:
                    raise ProfileError("unresolved_constraint", "unknown constraint type %r" % t)
                notes.append(why)
                if not ok:
                    raise ProfileError("invalid_mandate", why)
            l2_base = payment_payload["l2"].split("~", 1)[0]
            return VerificationResult(True, details={"reference": sdjwt.sd_hash(payment_payload["l3a"]), "amount": final["payment_amount"], "payee": final["payee"], "l2_pair_id": sha256_b64url(l2_base.encode("ascii")), "constraints": notes})
        except ProfileError as e:
            return VerificationResult(False, e.code, e.description)
        except (sdjwt.SDJWTError, KeyError, TypeError, ValueError) as e:
            return VerificationResult(False, "invalid_credential", "%s: %s" % (e.__class__.__name__, e))
