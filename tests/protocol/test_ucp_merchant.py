"""UCP business fixture + platform adapter: discovery, checkout lifecycle,
Complete Checkout idempotency, merchant-signed checkout verification."""
import pytest

from ucp_adapter import MerchantError, UCP_VERSION

ADDRESS = {"name": "Demo User", "line1": "1 Example Way", "city": "Springfield", "region": "IL", "postal_code": "62701", "country": "US"}
BUYER = {"full_name": "Demo User", "email": "user_demo@example.invalid"}


def test_discovery_is_pinned_and_publishes_keys(harness):
    ucp = harness.ctx.ucp
    profile = ucp.discover("merchant_a")
    assert profile["ucp"]["version"] == UCP_VERSION
    assert "dev.ucp.shopping.checkout" in profile["ucp"]["capabilities"]
    assert profile["keys"][0]["kid"] == harness.keys.kid("merchant_a")
    assert ucp.handler_id("merchant_a", "ap2") == "ap2_fixture_handler"
    assert ucp.handler_id("merchant_a", "vi") == "vi_fixture_handler"
    with pytest.raises(MerchantError):
        ucp.discover("merchant_zzz")


def test_feed_is_labelled_and_needs_a_destination_for_delivered_price(harness):
    ucp = harness.ctx.ucp
    q = ucp.feed_quote("merchant_a", "K1-BLK-US", 1, ADDRESS)
    assert q["source"] == "application_feed" and "tax_minor" in q and q["shipping_minor"] is not None
    q2 = ucp.feed_quote("merchant_a", "K1-BLK-US", 1, {"country": "US"})
    assert q2.get("tax_minor") is None and q2["shipping_minor"] is None


def test_checkout_create_verify_and_tamper_detection(harness):
    ucp = harness.ctx.ucp
    c = ucp.create_checkout("merchant_a", "K1-BLK-US", 1, BUYER, ADDRESS, idempotency_key="k1")
    assert c["status"] == "ready_for_complete" and c["currency"] == "USD"
    assert {t["type"] for t in c["totals"]} == {"subtotal", "fulfillment", "tax", "total"}
    payload = ucp.verify_checkout_jwt("merchant_a", c)
    assert payload["id"] == c["id"] and payload["merchant"]["id"] == "merchant_a"
    # create is idempotent
    assert ucp.create_checkout("merchant_a", "K1-BLK-US", 1, BUYER, ADDRESS, idempotency_key="k1")["id"] == c["id"]
    # tampering with the response body is detected against the signed checkout
    forged = dict(c, totals=[dict(t, amount=1) for t in c["totals"]])
    with pytest.raises(MerchantError) as e:
        ucp.verify_checkout_jwt("merchant_a", forged)
    assert e.value.code == "checkout_jwt_mismatch"
    # a checkout without an address cannot be completed autonomously
    c2 = ucp.create_checkout("merchant_a", "K1-BLK-US", 1, BUYER, {"country": "US"}, idempotency_key="k2")
    assert c2["status"] == "incomplete"
    assert any(m["code"] == "missing" for m in c2["messages"])
    # tax unknown → requires escalation, never auto-completed
    harness.set_price("merchant_a", "K1-BLK-US", tax_unknown=True)
    c3 = ucp.create_checkout("merchant_a", "K1-BLK-US", 1, BUYER, ADDRESS, idempotency_key="k3")
    assert c3["status"] == "requires_escalation" and not any(t["type"] == "total" for t in c3["totals"])


def test_complete_requires_idempotency_key_and_authorization(harness):
    client = harness.merchant_clients["merchant_a"]
    c = client.post("/checkout-sessions", json={"line_items": [{"item": {"id": "K1-BLK-US"}, "quantity": 1}], "buyer": BUYER, "fulfillment": {"address": ADDRESS}}).json()
    r = client.post("/checkout-sessions/%s/complete" % c["id"], json={"payment": {"instruments": []}})
    assert r.status_code == 400 and r.json()["code"] == "idempotency_key_required"
    body = {"payment": {"instruments": [{"id": "pi", "handler_id": "ap2_fixture_handler", "type": "ap2_mandate", "credential": {"type": "x", "token": "nope"}}]}}
    r = client.post("/checkout-sessions/%s/complete" % c["id"], json=body, headers={"Idempotency-Key": "abc"})
    assert r.status_code == 400 and r.json()["code"] == "authorization_required"
    body["ap2"] = {"checkout_mandate": "garbage"}
    r = client.post("/checkout-sessions/%s/complete" % c["id"], json=body, headers={"Idempotency-Key": "abc2"})
    assert r.status_code == 200 and r.json()["status"] == "ready_for_complete"
    assert r.json()["messages"][0]["code"] == "invalid_credential"
    assert "checkout_receipt" in r.json()["ap2"]  # error receipt returned
    # same key + different payload → 409
    body["ap2"] = {"checkout_mandate": "garbage2"}
    r = client.post("/checkout-sessions/%s/complete" % c["id"], json=body, headers={"Idempotency-Key": "abc2"})
    assert r.status_code == 409
    assert harness.merchants["merchant_a"].orders == {}


def test_cancel_and_expiry(harness):
    ucp = harness.ctx.ucp
    c = ucp.create_checkout("merchant_a", "K1-BLK-US", 1, BUYER, ADDRESS, idempotency_key="c1")
    assert ucp.cancel_checkout("merchant_a", c["id"])["status"] == "canceled"
    c2 = ucp.create_checkout("merchant_a", "K1-BLK-US", 1, BUYER, ADDRESS, idempotency_key="c2")
    harness.advance(hours=7)
    assert ucp.get_checkout("merchant_a", c2["id"])["status"] == "canceled"
