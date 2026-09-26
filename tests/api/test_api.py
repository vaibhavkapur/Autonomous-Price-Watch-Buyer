"""Application API: auth, ownership, versioned + idempotent mutations, demo controls."""
from datetime import timedelta

import pytest
from starlette.testclient import TestClient

from api.main import create_api
from harness import DEADLINE

AUTH = {"Authorization": "Bearer demo-token"}


@pytest.fixture
def api(harness):
    app = create_api(harness.ctx, harness.service)
    app.state.worker = harness.worker
    with TestClient(app) as c:
        yield c


def draft_body(**over):
    body = {
        "original_request": "Buy this exact keyboard if its delivered price drops below $100 before Sunday.",
        "product": {"product_id": "keyboard_k1_black_us", "title": "K1", "attributes": {"color": "black"}},
        "merchant_skus": {"merchant_a": "K1-BLK-US", "merchant_b": "KB-K1-US-B"},
        "quantity": 1, "currency": "USD", "price_rule": {"operator": "lt", "delivered_total_minor": 10000},
        "allowed_merchants": ["merchant_a", "merchant_b"], "destination_id": "address_1", "expires_at": DEADLINE.isoformat(),
        "timezone": "Asia/Kolkata", "max_purchases": 1, "authorization_profile": "ap2",
    }
    body.update(over)
    return body


def test_authentication_and_ownership(api):
    assert api.get("/v1/watches").status_code == 401
    assert api.get("/v1/watches", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert api.get("/v1/watches", headers=AUTH).json() == {"watches": []}
    assert api.get("/v1/watches/watch_x", headers=AUTH).status_code == 404


def test_parse_preview_create_propose_activate_flow(api, harness):
    p = api.post("/v1/watches/parse", json={"text": "Buy this exact keyboard if its delivered price drops below $100 before Sunday. Use only these two merchants and buy it once."}, headers=AUTH).json()
    assert p["price_rule"] == {"operator": "lt", "delivered_total_minor": 10000}
    pv = api.post("/v1/watches/preview", json=draft_body(destination_id=None), headers=AUTH).json()
    assert pv["inclusive_ceiling_minor"] == 9999 and pv["unknowns"]
    # mutations need an Idempotency-Key
    assert api.post("/v1/watches", json=draft_body(), headers=AUTH).status_code == 428
    r = api.post("/v1/watches", json=draft_body(), headers=dict(AUTH, **{"Idempotency-Key": "c1"}))
    assert r.status_code == 201, r.text
    w = r.json()
    assert w["status"] == "draft" and w["threshold_display"] == "USD 100.00" and "lease_token" not in w
    # replay returns the same watch; a different payload under the same key conflicts
    assert api.post("/v1/watches", json=draft_body(), headers=dict(AUTH, **{"Idempotency-Key": "c1"})).json()["id"] == w["id"]
    assert api.post("/v1/watches", json=draft_body(quantity=1, timezone="UTC"), headers=dict(AUTH, **{"Idempotency-Key": "c1"})).status_code == 409
    # bad sku mapping is rejected at creation
    bad = api.post("/v1/watches", json=draft_body(merchant_skus={"merchant_a": "K1-WHT-US", "merchant_b": "KB-K1-US-B"}), headers=dict(AUTH, **{"Idempotency-Key": "c2"}))
    assert bad.status_code == 422 and bad.json()["code"] == "sku_mapping_rejected"

    prop = api.post("/v1/watches/%s/authorization-proposal" % w["id"], headers=dict(AUTH, **{"Idempotency-Key": "p1"})).json()
    assert prop["can_activate"] and prop["profile_version"] == "ap2-v0.2"
    assert any("USD 99.99" in s for s in prop["rule"]["sentences"])
    assert prop["delegation"]["inclusive_max_minor"] == 9999
    w = api.get("/v1/watches/%s" % w["id"], headers=AUTH).json()
    assert w["status"] == "awaiting_authorization"

    # activation requires the expected version
    assert api.post("/v1/watches/%s/activate" % w["id"], headers=dict(AUTH, **{"Idempotency-Key": "a1"})).status_code == 428
    stale = api.post("/v1/watches/%s/activate" % w["id"], headers=dict(AUTH, **{"Idempotency-Key": "a0", "If-Match": str(w["version"] - 1)}))
    assert stale.status_code == 409
    act = api.post("/v1/watches/%s/activate" % w["id"], headers=dict(AUTH, **{"Idempotency-Key": "a1", "If-Match": str(w["version"])}))
    assert act.status_code == 200 and act.json()["status"] == "watching"
    detail = api.get("/v1/watches/%s" % w["id"], headers=AUTH).json()
    assert detail["authorization"]["profile"] == "ap2" and detail["authorization"]["status"] == "active"
    assert detail["clock"]["simulated"] is True

    # demo controls drive a purchase end to end
    api.post("/demo/merchants/merchant_a/price-scenario", json={"sku": "K1-BLK-US", "item_price_minor": 8900, "shipping_minor": 500, "tax_minor": 400}, headers=AUTH)
    res = api.post("/demo/watches/%s/evaluate-now" % w["id"], headers=AUTH).json()
    assert res["results"][0]["outcome"] == "purchased"
    obs = api.get("/v1/watches/%s/observations" % w["id"], headers=AUTH).json()["observations"]
    assert any(o["total_display"] == "USD 98.00" for o in obs)
    tl = api.get("/v1/watches/%s/timeline" % w["id"], headers=AUTH).json()
    assert "purchase.completed" in [e["type"] for e in tl["events"]]
    pur = api.get("/v1/watches/%s/purchase" % w["id"], headers=AUTH).json()
    assert pur["attempts"][0]["claim_state"] == "purchased"
    art = pur["evidence"][0]["artifact_id"]
    ev = api.get("/v1/watches/%s/evidence/%s" % (w["id"], art), headers=AUTH).json()
    assert ev["digest"] == pur["evidence"][0]["digest"]
    creds = [a for a in pur["evidence"] if a["kind"] == "ucp.complete_request"][0]
    redacted = api.get("/v1/watches/%s/evidence/%s" % (w["id"], creds["artifact_id"]), headers=AUTH).json()
    assert redacted["content"]["payment"]["instruments"][0]["credential"]["token"].startswith("<redacted:")
    m = api.get("/v1/metrics", headers=AUTH).json()["metrics"]
    assert m["purchases_completed"] == 1 and m["qualifying_offers"] >= 1
    assert api.get("/demo/merchants/merchant_a/orders", headers=AUTH).json()["orders"]


def test_cancel_and_pause_resume_via_api(api):
    r = api.post("/v1/watches", json=draft_body(), headers=dict(AUTH, **{"Idempotency-Key": "c1"}))
    w = r.json()
    api.post("/v1/watches/%s/authorization-proposal" % w["id"], headers=dict(AUTH, **{"Idempotency-Key": "p1"}))
    w = api.get("/v1/watches/%s" % w["id"], headers=AUTH).json()
    w = api.post("/v1/watches/%s/activate" % w["id"], headers=dict(AUTH, **{"Idempotency-Key": "a1", "If-Match": str(w["version"])})).json()
    w = api.post("/v1/watches/%s/pause" % w["id"], json={"expected_version": w["version"]}, headers=dict(AUTH, **{"Idempotency-Key": "z1"})).json()
    assert w["status"] == "paused"
    w = api.post("/v1/watches/%s/resume" % w["id"], json={"expected_version": w["version"]}, headers=dict(AUTH, **{"Idempotency-Key": "z2"})).json()
    assert w["status"] == "watching"
    w = api.post("/v1/watches/%s/cancel" % w["id"], json={"expected_version": w["version"]}, headers=dict(AUTH, **{"Idempotency-Key": "z3"})).json()
    assert w["status"] == "cancelled"
    again = api.post("/v1/watches/%s/cancel" % w["id"], json={"expected_version": w["version"]}, headers=dict(AUTH, **{"Idempotency-Key": "z4"}))
    assert again.status_code == 409


def test_demo_controls_disabled_outside_development(harness):
    harness.ctx.settings.profile = "production"
    app = create_api(harness.ctx, harness.service)
    with TestClient(app) as c:
        assert c.post("/demo/faults", json={"target": "adapter", "fault": "x"}, headers=AUTH).status_code in (404, 405)
        assert c.get("/v1/watches", headers=AUTH).status_code == 200
