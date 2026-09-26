"""Lost responses, restarts, stock/price changes at checkout (plan §18, Demo D)."""
from persistence import repo


def eligible_both(h, a_ship=500, b_item=9000):
    h.set_price("merchant_a", "K1-BLK-US", item_price_minor=8900, shipping_minor=a_ship, tax_minor=400)
    h.set_price("merchant_b", "KB-K1-US-B", item_price_minor=b_item, shipping_minor=500, tax_minor=400)


def test_demo_d_dropped_completion_response_recovers_without_buying_twice(harness):
    h = harness
    w = h.create_active_watch()
    eligible_both(h)
    h.add_adapter_fault("drop_complete_response")
    r = h.tick()
    assert r[0]["outcome"] == "reconciliation_required"
    cur = h.watch(w["id"])
    assert cur["status"] == "reconciliation_required" and cur["next_check_at"] is None
    with h.engine.begin() as conn:
        attempt = repo.attempts_for_watch(conn, w["id"])[0]
        case = repo.open_case_for_attempt(conn, attempt["id"])
        outbound = repo.outbound_for_attempt(conn, attempt["id"])
    assert attempt["claim_state"] == "reconciliation_required" and attempt["order_state"] == "unknown"
    assert case is not None and outbound[0]["state"] == "sent_unknown"
    # the merchant did place the order; the agent just never saw the response
    assert len(h.orders("merchant_a")) == 1
    # merchant_b still qualifies but the claim is retained: nothing else may be bought
    h.advance(minutes=1)
    h.worker.coordinator.reconcile_open = lambda: 0  # the old worker never gets to reconcile
    assert h.tick() == []
    assert len(h.orders("merchant_b")) == 0

    # --- "restart the worker": a fresh worker recovers from durable state
    restarted = h.new_worker("worker-restarted")
    summary = restarted.start()
    assert summary["reconciled"] >= 1
    cur = h.watch(w["id"])
    assert cur["status"] == "purchased"
    with h.engine.begin() as conn:
        attempt = repo.attempts_for_watch(conn, w["id"])[0]
        case = repo.list_cases(conn, w["id"])[0]
        events = [e["type"] for e in repo.list_events(conn, w["id"])]
    assert attempt["claim_state"] == "purchased" and attempt["external_references"]["order_id"] == h.orders("merchant_a")[0]["id"]
    assert case["state"] == "resolved" and case["resolution"] == "order_found"
    assert "purchase.outcome_unknown" in events and "reconciliation.checked" in events and "purchase.completed" in events
    assert (len(h.orders("merchant_a")), len(h.orders("merchant_b"))) == (1, 0)
    h.advance(minutes=5)
    restarted.tick()
    h.tick()
    assert (len(h.orders("merchant_a")), len(h.orders("merchant_b"))) == (1, 0)


def test_timeout_keeps_claim_and_blocks_other_merchant_until_reconciled(harness):
    h = harness
    w = h.create_active_watch()
    eligible_both(h)
    h.add_adapter_fault("drop_complete_response")
    h.worker.coordinator.reconcile_open = lambda: 0  # pretend the merchant stays unreachable
    h.tick()
    h.advance(minutes=1)
    r = h.tick()
    assert r == []  # not schedulable while reconciliation is pending
    with h.engine.begin() as conn:
        assert repo.active_attempt(conn, w["id"])["claim_state"] == "reconciliation_required"
        assert repo.all_metrics(conn)["unresolved_executions"] == 1
    assert len(h.orders("merchant_b")) == 0


def test_merchant_5xx_before_processing_is_resolved_by_identical_resubmission(harness):
    h = harness
    w = h.create_active_watch()
    eligible_both(h)
    h.merchant_clients["merchant_a"].post("/demo/faults", json={"fault": "fail_complete_once", "count": 1})
    r = h.tick()
    assert r[0]["outcome"] == "reconciliation_required"
    assert h.orders("merchant_a") == []
    # reconciliation: Get Checkout shows ready_for_complete → resend the identical request, same key
    n = h.worker.coordinator.reconcile_open()
    assert n == 1
    assert h.watch(w["id"])["status"] == "purchased"
    assert len(h.orders("merchant_a")) == 1
    events = h.event_types(w["id"])
    assert "reconciliation.resubmitted_identical_request" in events
    with h.engine.begin() as conn:
        attempt = repo.attempts_for_watch(conn, w["id"])[0]
    assert h.orders("merchant_a")[0]["idempotency_key"] == attempt["idempotency_key"]


def test_restart_before_submission_releases_claim_safely(harness):
    h = harness
    w = h.create_active_watch()
    eligible_both(h)
    (watch, token), = h.ctx.scheduler.claim_due()
    now = h.clock.now()
    with h.engine.begin() as conn:
        auth = repo.active_authorization(conn, w["id"])
        repo.update_watch_versioned(conn, w["id"], watch["version"], {"status": "candidate_found"}, lease_token=token, now=now)
        repo.update_watch_versioned(conn, w["id"], watch["version"] + 1, {"status": "checkout_validating"}, lease_token=token, now=now)
        watch = repo.get_watch(conn, w["id"])
    from purchase_coordinator import PurchaseCoordinator

    PurchaseCoordinator(h.ctx).claim(watch, token, None, auth, merchant_id="merchant_a", checkout_id="chk_x", checkout_payload={}, total_minor=9800, currency="USD")
    assert h.watch(w["id"])["status"] == "purchase_claimed"
    # worker dies here; a new worker starts
    restarted = h.new_worker("worker-2")
    summary = restarted.start()
    assert summary == {"reconciled": 0, "aborted": 1}
    cur = h.watch(w["id"])
    assert cur["status"] == "watching"
    with h.engine.begin() as conn:
        assert repo.attempts_for_watch(conn, w["id"])[0]["claim_state"] == "aborted"
        assert repo.active_authorization(conn, w["id"])["presentation_state"] == "idle"
    h.advance(seconds=h.settings.lease_seconds + 1)
    r = restarted.tick()
    assert r[0]["outcome"] == "purchased"
    assert len(h.orders("merchant_a")) + len(h.orders("merchant_b")) == 1


def test_stock_disappears_between_feed_and_checkout(harness):
    h = harness
    w = h.create_active_watch(allowed_merchants=["merchant_a"], merchant_skus={"merchant_a": "K1-BLK-US"})
    h.set_price("merchant_a", "K1-BLK-US", item_price_minor=8900, shipping_minor=500, tax_minor=400)
    original = h.ctx.ucp.create_checkout

    def sold_out(*a, **k):
        h.set_price("merchant_a", "K1-BLK-US", stock=0)
        return original(*a, **k)

    h.ctx.ucp.create_checkout = sold_out
    r = h.tick()
    assert r[0]["outcome"] == "candidates_rejected_at_checkout"
    assert any("checkout_status:incomplete" in x or "checkout_message:out_of_stock" in x for x in r[0]["rejected"][0]["reasons"])
    cur = h.watch(w["id"])
    assert cur["status"] == "watching" and cur["next_check_at"] is not None
    assert h.orders("merchant_a") == []
    assert "checkout.rejected" in h.event_types(w["id"])


def test_shipping_changes_at_checkout_are_rejected_and_watching_continues(harness):
    h = harness
    w = h.create_active_watch(allowed_merchants=["merchant_a"], merchant_skus={"merchant_a": "K1-BLK-US"})
    h.set_price("merchant_a", "K1-BLK-US", item_price_minor=8900, shipping_minor=500, tax_minor=400)  # feed: 9800
    original = h.ctx.ucp.create_checkout

    def pricier(*a, **k):
        h.set_price("merchant_a", "K1-BLK-US", shipping_minor=1200)  # checkout: 10500
        return original(*a, **k)

    h.ctx.ucp.create_checkout = pricier
    r = h.tick()
    assert r[0]["outcome"] == "candidates_rejected_at_checkout"
    assert any(x.startswith("price_rule_not_met total=10500") for x in r[0]["rejected"][0]["reasons"])
    with h.engine.begin() as conn:
        obs = repo.list_observations(conn, w["id"])
    checkout_obs = [o for o in obs if o["source_reference"].startswith("ucp_checkout")][0]
    assert checkout_obs["eligibility"] == "rejected" and checkout_obs["total_minor"] == 10500
    assert checkout_obs["price_components"]["shipping"] == 1200
    assert h.watch(w["id"])["status"] == "watching"
    # merchant checkout was cancelled, not left dangling
    state = h.merchant_clients["merchant_a"].get("/demo/state").json()
    assert state["orders"] == 0
    h.ctx.ucp.create_checkout = original
    h.advance(minutes=1)
    h.set_price("merchant_a", "K1-BLK-US", shipping_minor=500)
    assert h.tick()[0]["outcome"] == "purchased"


def test_merchant_rejection_receipt_allows_ap2_retry_but_consumes_vi(harness):
    """After a verifier rejection with a receipt, AP2 authority can be re-presented;
    a VI L2 pair is single-use and the watch pauses for new consent."""
    h = harness
    for profile, expected_status in (("ap2", "watching"), ("vi", "paused")):
        w = h.create_active_watch(authorization_profile=profile, allowed_merchants=["merchant_a"], merchant_skus={"merchant_a": "K1-BLK-US"})
        h.set_price("merchant_a", "K1-BLK-US", item_price_minor=8900, shipping_minor=500, tax_minor=400)
        original = h.ctx.ucp.complete_checkout

        def rejected(merchant_id, checkout_id, payload, key, _o=original):
            # merchant refuses because the evidence names a different checkout (simulated tampering en route)
            bad = dict(payload)
            if "ap2" in bad:
                bad["ap2"] = {"checkout_mandate": bad["ap2"]["checkout_mandate"][:-5] + "AAAA~"}
            else:
                bad["vi"] = dict(bad["vi"], l3b=bad["vi"]["l3b"][:-5] + "AAAA~")
            return _o(merchant_id, checkout_id, bad, key)

        h.ctx.ucp.complete_checkout = rejected
        r = h.tick()
        h.ctx.ucp.complete_checkout = original
        assert r[0]["outcome"] == "rejected", r
        assert h.watch(w["id"])["status"] == expected_status
        assert h.orders("merchant_a") == []
        with h.engine.begin() as conn:
            auth = repo.list_authorizations(conn, w["id"])[0]
        assert auth["presentation_state"] == "rejected"
        assert auth["status"] == ("active" if profile == "ap2" else "consumed")
        cur = h.watch(w["id"])
        h.service.cancel(cur, cur["version"])
