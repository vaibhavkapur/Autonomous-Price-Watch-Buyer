"""Authorization profile fixtures: AP2 v0.2 and VI v0.1-draft each get their own
tests. Both must accept a conforming presentation and reject tampering, scope
violations, expiry and re-presentation."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from authorization_profiles import sdjwt
from authorization_profiles.base import ConsentRequest, ProfileError
from authorization_profiles.keys import KeyRing
from authorization_profiles.payment_sim import CredentialProviderSim, MerchantPaymentProcessorSim
from authorization_profiles.registry import PROFILE_VERSIONS, get_profile
from common.util import b64url_decode, b64url_encode

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
MERCHANTS = [
    {"id": "merchant_a", "name": "Keyboard Depot", "website": "https://keyboard-depot.fixture.local"},
    {"id": "merchant_b", "name": "ClickClack Supply", "website": "https://clickclack.fixture.local"},
]
ITEMS = [{"id": "K1-BLK-US", "title": "K1 (Black, US)", "merchant_id": "merchant_a"}, {"id": "KB-K1-US-B", "title": "K1 US Black", "merchant_id": "merchant_b"}]


@pytest.fixture(scope="module")
def ring():
    return KeyRing.generate()


def consent_request():
    return ConsentRequest("watch_1", 1, "user_demo", "agent_1", MERCHANTS, ITEMS, 1, "USD", 9999, NOW + timedelta(days=1), NOW, {"type": "card", "id": "pi_1", "description": "Card ••••4242"}, "Buy the K1 below $100")


def make_checkout(ring, merchant="merchant_a", sku="K1-BLK-US", total=9800, qty=1):
    m = next(x for x in MERCHANTS if x["id"] == merchant)
    checkout = {
        "id": "chk_1", "merchant": m, "currency": "USD", "status": "ready_for_complete", "expires_at": "2026-09-26T18:00:00Z",
        "line_items": [{"id": "li_1", "item": {"id": sku, "title": "K1", "price": 8900}, "quantity": qty}],
        "totals": [{"type": "subtotal", "amount": 8900}, {"type": "fulfillment", "amount": 500}, {"type": "tax", "amount": 400}, {"type": "total", "amount": total}],
    }
    jwt = sdjwt.sign_jwt({"alg": "ES256", "typ": "JWT", "kid": ring.kid(merchant)}, checkout, ring.private(merchant))
    return checkout, jwt, m


@pytest.mark.parametrize("name", ["ap2", "vi"])
def test_round_trip_merchant_and_payment(ring, name):
    trust = ring.trust_store()
    p = get_profile(name, ring, trust)
    assert p.version == PROFILE_VERSIONS[name]
    consent = p.issue_consent(consent_request())
    assert consent.profile == name and consent.expires_at == NOW + timedelta(days=1)
    checkout, jwt, m = make_checkout(ring)
    prep = p.prepare(consent.native, jwt, checkout, m, 9800, "USD", NOW, m["website"])
    # merchant role
    r = p.verify_for_merchant(prep.merchant_payload, jwt, m, NOW)
    assert r.ok, (r.code, r.description)
    assert r.details["checkout_hash"] == prep.checkout_hash
    # credential provider / network role + MPP capture
    cp = CredentialProviderSim(ring, trust)
    a = cp.authorize(name, prep.payment_payload, prep.checkout_hash, NOW)
    assert a["ok"]
    receipt_claims = sdjwt.verify_jwt(a["payment_receipt"], trust.key("credential_provider"))[1]
    assert receipt_claims["status"] == "Success" and receipt_claims["reference"] == a["reference"]
    mpp = MerchantPaymentProcessorSim(trust)
    cap = mpp.capture(a["credential"], checkout_hash=prep.checkout_hash, amount_minor=9800, currency="USD", merchant_id="merchant_a", now=NOW)
    assert cap["ok"]
    assert not mpp.capture(a["credential"], checkout_hash="other", amount_minor=9800, currency="USD", merchant_id="merchant_a", now=NOW)["ok"]


@pytest.mark.parametrize("name", ["ap2", "vi"])
def test_no_second_presentation_without_rejection(ring, name):
    trust = ring.trust_store()
    p = get_profile(name, ring, trust)
    consent = p.issue_consent(consent_request())
    checkout, jwt, m = make_checkout(ring)
    prep = p.prepare(consent.native, jwt, checkout, m, 9800, "USD", NOW, m["website"])
    cp = CredentialProviderSim(ring, trust)
    assert cp.authorize(name, prep.payment_payload, prep.checkout_hash, NOW)["ok"]
    again = cp.authorize(name, prep.payment_payload, prep.checkout_hash, NOW)
    assert not again["ok"] and again["error"] == "invalid_mandate"
    # a *different* checkout under the same open mandate pair is also refused by the network for VI
    checkout2, jwt2, _ = make_checkout(ring, total=9700)
    prep2 = p.prepare(consent.native, jwt2, checkout2, m, 9700, "USD", NOW, m["website"])
    second = cp.authorize(name, prep2.payment_payload, prep2.checkout_hash, NOW)
    if name == "vi":
        assert not second["ok"]  # one L3 pair per L2 pair
    else:
        assert second["ok"]  # AP2: agent-side rule; the CP sees a new closed mandate (rejection receipt handling lives in the coordinator)


@pytest.mark.parametrize("name", ["ap2", "vi"])
def test_amount_range_ceiling_is_inclusive_max(ring, name):
    trust = ring.trust_store()
    p = get_profile(name, ring, trust)
    consent = p.issue_consent(consent_request())
    checkout, jwt, m = make_checkout(ring, total=9999)
    prep = p.prepare(consent.native, jwt, checkout, m, 9999, "USD", NOW, m["website"])
    assert CredentialProviderSim(ring, trust).authorize(name, prep.payment_payload, prep.checkout_hash, NOW)["ok"]
    checkout, jwt, m = make_checkout(ring, total=10000)
    prep = p.prepare(consent.native, jwt, checkout, m, 10000, "USD", NOW, m["website"])
    res = CredentialProviderSim(ring, trust).authorize(name, prep.payment_payload, prep.checkout_hash, NOW)
    assert not res["ok"] and "amount_range" in res["error_description"]


@pytest.mark.parametrize("name", ["ap2", "vi"])
def test_wrong_merchant_or_item_is_outside_scope(ring, name):
    trust = ring.trust_store()
    p = get_profile(name, ring, trust)
    consent = p.issue_consent(consent_request())
    checkout, jwt, m = make_checkout(ring, sku="K1-WHT-US")
    with pytest.raises(ProfileError):
        p.prepare(consent.native, jwt, checkout, m, 9800, "USD", NOW, m["website"])
    other = {"id": "merchant_c", "name": "Nope", "website": "https://nope.local"}
    checkout, jwt, _ = make_checkout(ring)
    with pytest.raises(ProfileError):
        p.prepare(consent.native, jwt, checkout, other, 9800, "USD", NOW, other["website"])
    # a merchant that receives evidence meant for another merchant must reject it
    prep = p.prepare(consent.native, jwt, checkout, m, 9800, "USD", NOW, m["website"])
    r = p.verify_for_merchant(prep.merchant_payload, jwt, MERCHANTS[1], NOW)
    assert not r.ok


@pytest.mark.parametrize("name", ["ap2", "vi"])
def test_checkout_jwt_substitution_detected(ring, name):
    trust = ring.trust_store()
    p = get_profile(name, ring, trust)
    consent = p.issue_consent(consent_request())
    checkout, jwt, m = make_checkout(ring)
    prep = p.prepare(consent.native, jwt, checkout, m, 9800, "USD", NOW, m["website"])
    _, other_jwt, _ = make_checkout(ring, total=9700)
    r = p.verify_for_merchant(prep.merchant_payload, other_jwt, m, NOW)
    assert not r.ok and r.code == "invalid_mandate"


@pytest.mark.parametrize("name", ["ap2", "vi"])
def test_expired_authority_is_rejected(ring, name):
    trust = ring.trust_store()
    p = get_profile(name, ring, trust)
    consent = p.issue_consent(consent_request())
    checkout, jwt, m = make_checkout(ring)
    prep = p.prepare(consent.native, jwt, checkout, m, 9800, "USD", NOW, m["website"])
    later = NOW + timedelta(days=1, minutes=10)
    assert not p.verify_for_merchant(prep.merchant_payload, jwt, m, later).ok
    assert not CredentialProviderSim(ring, trust).authorize(name, prep.payment_payload, prep.checkout_hash, later)["ok"]
    # closed/L3 evidence is short-lived even while the open mandate is valid
    soon = NOW + timedelta(minutes=20)
    assert not p.verify_for_merchant(prep.merchant_payload, jwt, m, soon).ok


def _tamper_disclosure(chain: str, predicate, mutate) -> str:
    parts = chain.split("~")
    for i, part in enumerate(parts):
        if part and "." not in part:
            arr = json.loads(b64url_decode(part))
            if predicate(arr):
                mutate(arr)
                parts[i] = b64url_encode(json.dumps(arr, separators=(",", ":")).encode())
    return "~".join(parts)


def test_ap2_unknown_constraint_fails_and_tampered_disclosure_detected(ring):
    trust = ring.trust_store()
    p = get_profile("ap2", ring, trust)
    consent = p.issue_consent(consent_request())
    checkout, jwt, m = make_checkout(ring)
    prep = p.prepare(consent.native, jwt, checkout, m, 9800, "USD", NOW, m["website"])
    chain = prep.merchant_payload["checkout_mandate"]
    # editing a disclosure changes its digest → no longer referenced → strict resolution fails
    tampered = _tamper_disclosure(chain, lambda a: isinstance(a[-1], dict) and a[-1].get("id") == "merchant_a", lambda a: a[-1].update(id="merchant_b"))
    r = p.verify_for_merchant({"checkout_mandate": tampered}, jwt, m, NOW)
    assert not r.ok and r.code == "invalid_credential"
    # unknown constraint types must fail evaluation (spec: treated as failing)
    req = consent_request()
    consent2 = p.issue_consent(req)
    d = consent2.native["disclosures"]
    arr = json.loads(b64url_decode(d["checkout_mandate"]))
    arr[1]["constraints"].append({"type": "checkout.something_new", "x": 1})
    # re-sign the delegate SD-JWT with the modified mandate (simulating a newer trusted surface)
    from authorization_profiles.sdjwt import Disclosure, element_ref

    new_disc = Disclosure.for_element(arr[1])
    hdr, payload = sdjwt.decode_unverified(consent2.native["issuer_jwt"])
    payload["delegate_payload"][0] = element_ref(new_disc)
    consent2.native["issuer_jwt"] = sdjwt.sign_jwt(hdr, payload, ring.private("agent_provider"))
    d["checkout_mandate"] = new_disc.encoded
    prep2 = p.prepare(consent2.native, jwt, checkout, m, 9800, "USD", NOW, m["website"])
    r2 = p.verify_for_merchant(prep2.merchant_payload, jwt, m, NOW)
    assert not r2.ok and r2.code == "unresolved_constraint"


def test_ap2_untrusted_provider_key_rejected(ring):
    other = KeyRing.generate()
    p_other = get_profile("ap2", other, other.trust_store())
    consent = p_other.issue_consent(consent_request())
    checkout, jwt, m = make_checkout(ring)
    prep = p_other.prepare(consent.native, jwt, checkout, m, 9800, "USD", NOW, m["website"])
    verifier = get_profile("ap2", KeyRing({}), ring.trust_store())
    r = verifier.verify_for_merchant(prep.merchant_payload, jwt, m, NOW)
    assert not r.ok and r.code == "invalid_credential"


def test_vi_structural_rules(ring):
    trust = ring.trust_store()
    p = get_profile("vi", ring, trust)
    consent = p.issue_consent(consent_request())
    checkout, jwt, m = make_checkout(ring)
    prep = p.prepare(consent.native, jwt, checkout, m, 9800, "USD", NOW, m["website"])
    vi = prep.merchant_payload["vi"]
    # L3b bound to a different L2 view (payment view) must fail the selective sd_hash check
    bad = {"vi": dict(vi, l2=prep.native["l2_payment_view"])}
    r = p.verify_for_merchant(bad, jwt, m, NOW)
    assert not r.ok and r.code in ("invalid_credential",)
    # L1 from an untrusted issuer
    other = KeyRing.generate()
    p_other = get_profile("vi", other, other.trust_store())
    consent2 = p_other.issue_consent(consent_request())
    prep2 = p_other.prepare(consent2.native, jwt, checkout, m, 9800, "USD", NOW, m["website"])
    assert not p.verify_for_merchant(prep2.merchant_payload, jwt, m, NOW).ok
    # typ values pinned by the draft
    hdr_l1, _ = sdjwt.decode_unverified(vi["l1"].split("~")[0])
    hdr_l2, _ = sdjwt.decode_unverified(vi["l2"].split("~")[0])
    hdr_l3, l3_payload = sdjwt.decode_unverified(vi["l3b"].split("~")[0])
    assert (hdr_l1["typ"], hdr_l2["typ"], hdr_l3["typ"]) == ("sd+jwt", "kb-sd-jwt+kb", "kb-sd-jwt")
    assert "cnf" not in l3_payload and hdr_l3["kid"] == trust.by_role["agent"]["kid"]


def test_profiles_are_not_interchangeable(ring):
    """AP2 artifacts presented as VI (and vice versa) are rejected, never relabelled."""
    trust = ring.trust_store()
    ap2 = get_profile("ap2", ring, trust)
    vi = get_profile("vi", ring, trust)
    checkout, jwt, m = make_checkout(ring)
    c1 = ap2.issue_consent(consent_request())
    prep1 = ap2.prepare(c1.native, jwt, checkout, m, 9800, "USD", NOW, m["website"])
    assert not vi.verify_for_merchant(prep1.merchant_payload, jwt, m, NOW).ok
    c2 = vi.issue_consent(consent_request())
    prep2 = vi.prepare(c2.native, jwt, checkout, m, 9800, "USD", NOW, m["website"])
    assert not ap2.verify_for_merchant(prep2.merchant_payload, jwt, m, NOW).ok
