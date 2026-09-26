"""Demos A–D (plan §23) executed from the reproducible price-scenario fixtures,
for both authorization profiles, through real UCP exchanges with both merchant
fixtures."""
import json
from pathlib import Path

import pytest

from authorization_profiles import sdjwt
from persistence import repo

SCENARIOS = Path(__file__).resolve().parents[2] / "fixtures" / "price-scenarios"


def load(name):
    return json.loads((SCENARIOS / name).read_text())


def apply(h, scenario):
    for step in scenario["steps"]:
        h.set_price(step["merchant"], step["sku"], **{k: v for k, v in step.items() if k not in ("merchant", "sku")})
    for f in scenario.get("faults", []):
        if f["target"] == "adapter":
            h.add_adapter_fault(f["fault"], f["count"])
        else:
            h.merchant_clients[f["target"]].post("/demo/faults", json={"fault": f["fault"], "count": f["count"]})


@pytest.mark.parametrize("profile", ["ap2", "vi"])
def test_demo_a_deceptive_item_price(harness, profile):
    h = harness
    sc = load("demo_a_deceptive_item_price.json")
    w = h.create_active_watch(authorization_profile=profile, allowed_merchants=["merchant_a"], merchant_skus={"merchant_a": "K1-BLK-US"})
    apply(h, sc)
    r = h.tick()
    assert r[0]["outcome"] == "no_candidate"
    obs = h.service.observations(w["id"])[0]
    assert obs["total_minor"] == sc["expect"]["delivered_total_minor"] == 10500
    assert obs["eligibility"] == "rejected"
    assert obs["price_components"]["item_subtotal"] == 8900 and obs["price_components"]["shipping"] == 1200 and obs["price_components"]["tax"] == 400
    assert obs["reasons"] == ["price_rule_not_met total=10500 operator=lt threshold=10000"]
    assert h.orders("merchant_a") == []


@pytest.mark.parametrize("profile", ["ap2", "vi"])
def test_demo_b_valid_drop_purchases_once_with_verifiable_evidence(harness, profile):
    h = harness
    sc = load("demo_b_valid_drop.json")
    w = h.create_active_watch(authorization_profile=profile, allowed_merchants=["merchant_a"], merchant_skus={"merchant_a": "K1-BLK-US"})
    apply(h, sc)
    r = h.tick()
    assert r[0]["outcome"] == "purchased"
    orders = h.orders("merchant_a")
    assert len(orders) == 1
    order = orders[0]
    assert next(t["amount"] for t in order["totals"] if t["type"] == "total") == 9800
    assert order["authorization"]["profile"] == profile
    assert order["line_items"][0]["item"]["id"] == "K1-BLK-US" and order["line_items"][0]["quantity"] == 1

    detail = h.service.purchase(w["id"])
    attempt = detail["attempts"][0]
    assert attempt["claim_state"] == "purchased" and attempt["payment_state"] == "captured" and attempt["final_total_minor"] == 9800
    assert attempt["external_references"]["order_id"] == order["id"]
    kinds = {e["kind"] for e in detail["evidence"]}
    expected = {"%s.consent" % profile, "%s.prepared_presentation" % profile, "%s.payment_receipt" % profile, "ucp.complete_request", "ucp.completed_checkout"}
    assert expected <= kinds
    if profile == "ap2":
        assert "ap2.checkout_receipt" in kinds
    else:
        assert "vi.merchant_result" in kinds

    # the merchant's verification log shows deterministic verification of the presented evidence
    log = h.merchant_clients["merchant_a"].get("/demo/state").json()["verification_log"]
    assert log == [dict(log[0], ok=True, profile=profile)]

    # evidence bundle: receipts verify against the merchant / credential-provider keys and reference the closed evidence
    with h.engine.begin() as conn:
        arts = h.ctx.vault.load_many(conn, attempt_id=attempt["id"])
    if profile == "ap2":
        receipt = next(a["content"]["checkout_receipt"] for a in arts.values() if a["kind"] == "ap2.checkout_receipt")
        claims = sdjwt.verify_jwt(receipt, h.trust.key("merchant_a"))[1]
        prepared = next(a["content"] for a in arts.values() if a["kind"] == "ap2.prepared_presentation")
        from authorization_profiles.ap2 import _split_chain

        segs = _split_chain(prepared["checkout_mandate_chain"])
        assert claims["status"] == "Success" and claims["order_id"] == order["id"]
        assert claims["reference"] == sdjwt.sd_hash(sdjwt.serialize(segs[1][0], segs[1][1]))
    pay = next(a["content"]["payment_receipt"] for a in arts.values() if a["kind"] == "%s.payment_receipt" % profile)
    assert sdjwt.verify_jwt(pay, h.trust.key("credential_provider"))[1]["status"] == "Success"

    # the user is notified once
    with h.engine.begin() as conn:
        notes = repo.list_notifications(conn, w["id"])
    assert [n["dedupe_key"].split(":")[-1] for n in notes] == ["purchased"]
    assert h.watch(w["id"])["status"] == "purchased"


@pytest.mark.parametrize("profile", ["ap2", "vi"])
def test_demo_c_concurrent_opportunity(harness, profile):
    h = harness
    sc = load("demo_c_concurrent.json")
    h.create_active_watch(authorization_profile=profile)
    apply(h, sc)
    r = h.tick()
    assert r[0]["outcome"] == "purchased"
    winner = sc["expect"]["winner"]
    assert len(h.orders(winner)) == 1
    other = "merchant_a" if winner == "merchant_b" else "merchant_b"
    assert h.orders(other) == []
    assert next(t["amount"] for t in h.orders(winner)[0]["totals"] if t["type"] == "total") == sc["expect"]["delivered_total_minor"]


@pytest.mark.parametrize("profile", ["ap2", "vi"])
def test_demo_d_recovery(harness, profile):
    h = harness
    sc = load("demo_d_recovery.json")
    w = h.create_active_watch(authorization_profile=profile)
    apply(h, sc)
    r = h.tick()
    assert r[0]["outcome"] == "reconciliation_required"
    assert h.watch(w["id"])["status"] == "reconciliation_required"
    restarted = h.new_worker("worker-after-restart")
    restarted.start()
    assert h.watch(w["id"])["status"] == sc["expect"]["final_status"] == "purchased"
    assert len(h.orders("merchant_a")) == sc["expect"]["orders_merchant_a"] == 1
    assert len(h.orders("merchant_b")) == sc["expect"]["orders_merchant_b"] == 0
    h.advance(minutes=10)
    restarted.tick()
    assert (len(h.orders("merchant_a")), len(h.orders("merchant_b"))) == (1, 0)
