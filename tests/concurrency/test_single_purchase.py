"""Single-purchase guarantee under concurrency (plan §17, Demo C)."""
import threading
from datetime import timedelta

import pytest

from persistence import repo
from purchase_coordinator import ClaimConflict, PurchaseCoordinator
from watch_domain.states import WatchStatus


def both_eligible(h):
    h.set_price("merchant_a", "K1-BLK-US", item_price_minor=8900, shipping_minor=500, tax_minor=400)  # 9800
    h.set_price("merchant_b", "KB-K1-US-B", item_price_minor=8700, shipping_minor=600, tax_minor=400)  # 9700


def test_demo_c_both_merchants_qualify_one_winner_one_order(harness):
    h = harness
    w = h.create_active_watch()
    both_eligible(h)
    r = h.tick()
    assert r[0]["outcome"] == "purchased"
    a, b = h.orders("merchant_a"), h.orders("merchant_b")
    assert (len(a), len(b)) == (0, 1), "cheaper delivered total wins"
    with h.engine.begin() as conn:
        obs = repo.list_observations(conn, w["id"])
        events = repo.list_events(conn, w["id"])
    eligible = sorted((o["merchant_id"], o["total_minor"]) for o in obs if o["eligibility"] == "eligible" and o["source_reference"].startswith("merchant_"))
    assert eligible == [("merchant_a", 9800), ("merchant_b", 9700)]
    ranking = next(e for e in events if e["type"] == "candidate.found")["details"]["ranking"]
    assert [x["merchant_id"] for x in ranking] == ["merchant_b", "merchant_a"]
    assert h.watch(w["id"])["status"] == "purchased"
    # later ticks buy nothing else even though merchant_a still qualifies
    h.advance(minutes=5)
    assert h.tick() == []
    assert (len(h.orders("merchant_a")), len(h.orders("merchant_b"))) == (0, 1)


def test_tie_breaks_deterministically_on_merchant_id(harness):
    h = harness
    h.create_active_watch()
    h.set_price("merchant_a", "K1-BLK-US", item_price_minor=8900, shipping_minor=500, tax_minor=400)
    h.set_price("merchant_b", "KB-K1-US-B", item_price_minor=8900, shipping_minor=500, tax_minor=400)
    h.tick()
    assert (len(h.orders("merchant_a")), len(h.orders("merchant_b"))) == (1, 0)


def _prepare_claimable(h):
    """Put a watch into checkout_validating with a lease so claims can be attempted directly."""
    w = h.create_active_watch()
    (watch, token), = h.ctx.scheduler.claim_due()
    now = h.clock.now()
    with h.engine.begin() as conn:
        repo.update_watch_versioned(conn, w["id"], watch["version"], {"status": WatchStatus.CANDIDATE_FOUND.value}, lease_token=token, now=now)
        repo.update_watch_versioned(conn, w["id"], watch["version"] + 1, {"status": WatchStatus.CHECKOUT_VALIDATING.value}, lease_token=token, now=now)
        auth = repo.active_authorization(conn, w["id"])
        watch = repo.get_watch(conn, w["id"])
    return watch, token, auth


def test_simultaneous_claims_yield_exactly_one_execution_claim(harness):
    h = harness
    watch, token, auth = _prepare_claimable(h)
    coord = PurchaseCoordinator(h.ctx)
    results = {}
    barrier = threading.Barrier(2)

    def attempt(name, merchant):
        barrier.wait()
        try:
            a = coord.claim(watch, token, None, auth, merchant_id=merchant, checkout_id="chk_%s" % name, checkout_payload={"m": merchant}, total_minor=9800, currency="USD")
            results[name] = ("claimed", a["id"])
        except ClaimConflict as e:
            results[name] = ("conflict", str(e))

    threads = [threading.Thread(target=attempt, args=("A", "merchant_a")), threading.Thread(target=attempt, args=("B", "merchant_b"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    states = sorted(v[0] for v in results.values())
    assert states == ["claimed", "conflict"], results
    with h.engine.begin() as conn:
        attempts = repo.attempts_for_watch(conn, watch["id"])
        metrics = repo.all_metrics(conn)
    assert len(attempts) == 1 and attempts[0]["claim_state"] == "claimed"
    assert metrics["duplicate_claims_prevented"] >= 1
    assert h.watch(watch["id"])["status"] == "purchase_claimed"


def test_database_index_blocks_second_active_claim_even_without_version_check(harness):
    """Belt and braces: even if application checks were bypassed, the partial unique
    index refuses a second active claim row."""
    from sqlalchemy.exc import IntegrityError

    h = harness
    watch, token, auth = _prepare_claimable(h)
    base = {"watch_id": watch["id"], "watch_version": 1, "authorization_id": auth["id"], "merchant_id": "merchant_a", "checkout_id": "c", "checkout_digest": "d", "final_total_minor": 1, "currency": "USD", "order_state": "none", "payment_state": "none", "external_references": {}, "claimed_at": h.clock.now()}
    with h.engine.begin() as conn:
        repo.insert_attempt(conn, dict(base, id="att_1", idempotency_key="k1", claim_state="claimed"))
    with pytest.raises(IntegrityError):
        with h.engine.begin() as conn:
            repo.insert_attempt(conn, dict(base, id="att_2", idempotency_key="k2", claim_state="submitting"))
    # a resolved (non-active) attempt does not block a new claim
    with h.engine.begin() as conn:
        repo.update_attempt_state(conn, "att_1", ["claimed"], claim_state="aborted")
        repo.insert_attempt(conn, dict(base, id="att_3", idempotency_key="k3", claim_state="claimed"))


def test_two_workers_ticking_concurrently_produce_one_order(harness):
    h = harness
    h.create_active_watch()
    both_eligible(h)
    other = h.new_worker("worker-2")
    other.start()
    barrier = threading.Barrier(2)
    outcomes = []

    def run(worker):
        barrier.wait()
        outcomes.extend(worker.tick())

    ts = [threading.Thread(target=run, args=(h.worker,)), threading.Thread(target=run, args=(other,))]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sum(1 for o in outcomes if o["outcome"] == "purchased") == 1
    assert len(h.orders("merchant_a")) + len(h.orders("merchant_b")) == 1


def test_cancellation_wins_when_serialized_before_submission(harness):
    h = harness
    w = h.create_active_watch()
    both_eligible(h)
    original = h.ctx.credential_provider.authorize

    def cancel_then_authorize(*a, **k):
        # the user cancels while the agent is preparing payment authorization
        cur = h.watch(w["id"])
        h.service.cancel(cur, cur["version"])
        return original(*a, **k)

    h.ctx.credential_provider.authorize = cancel_then_authorize
    r = h.tick()
    assert r[0]["outcome"] == "aborted" and r[0]["reason"] == "cancelled_before_submission"
    assert h.watch(w["id"])["status"] == "cancelled"
    assert h.orders("merchant_a") == [] and h.orders("merchant_b") == []
    with h.engine.begin() as conn:
        attempts = repo.attempts_for_watch(conn, w["id"])
    assert attempts[0]["claim_state"] == "aborted"
    assert "purchase.aborted_before_submission" in h.event_types(w["id"])


def test_cancel_after_submission_is_reported_as_not_recallable(harness):
    h = harness
    w = h.create_active_watch()
    both_eligible(h)
    h.add_adapter_fault("drop_complete_response")
    h.tick()
    cur = h.watch(w["id"])
    assert cur["status"] == "reconciliation_required"
    cur = h.service.cancel(cur, cur["version"])
    assert cur["cancel_requested"] and cur["status"] == "reconciliation_required"
    ev = [e for e in h.events(w["id"]) if e["type"] == "watch.cancel_requested"][0]
    assert "may not be cancellable" in ev["details"]["note"]


def test_expired_authorization_blocks_submission(harness):
    h = harness
    w = h.create_active_watch()
    both_eligible(h)
    original = h.ctx.credential_provider.authorize

    def expire_then_authorize(*a, **k):
        with h.engine.begin() as conn:
            auth = repo.active_authorization(conn, w["id"])
            repo.update_authorization(conn, auth["id"], expires_at=(h.clock.now() - timedelta(seconds=1)).replace(tzinfo=None))
        return original(*a, **k)

    h.ctx.credential_provider.authorize = expire_then_authorize
    r = h.tick()
    assert r[0]["outcome"] == "aborted" and r[0]["reason"] == "authorization_expired_before_submission"
    assert h.orders("merchant_a") == [] and h.orders("merchant_b") == []
    assert h.watch(w["id"])["status"] == "paused"
    assert "authorization.required" in h.event_types(w["id"])


def test_deadline_passing_between_claim_and_submission_blocks_submission(harness):
    h = harness
    w = h.create_active_watch()
    both_eligible(h)
    original = h.ctx.credential_provider.authorize

    def late(*a, **k):
        h.clock.set(w["expires_at"] + timedelta(seconds=1))
        return original(*a, **k)

    h.ctx.credential_provider.authorize = late
    r = h.tick()
    assert r[0]["outcome"] == "aborted" and r[0]["reason"] == "deadline_passed_before_submission"
    assert h.watch(w["id"])["status"] == "expired"
    assert h.orders("merchant_a") == [] and h.orders("merchant_b") == []


def test_presented_mandate_is_not_represented_without_receipt(harness):
    h = harness
    w = h.create_active_watch()
    both_eligible(h)
    h.add_adapter_fault("drop_complete_response")
    h.tick()
    with h.engine.begin() as conn:
        auth = repo.active_authorization(conn, w["id"])
    assert auth["presentation_state"] == "pending"
    # even if someone forced the watch back to watching, the evaluator refuses to present again
    cur = h.watch(w["id"])
    with h.engine.begin() as conn:
        repo.update_watch_versioned(conn, w["id"], cur["version"], {"status": "watching", "next_check_at": h.clock.now()})
    h.worker.coordinator.reconcile_open = lambda: 0  # keep the receipt outstanding for this tick
    r = h.tick()
    assert r and r[-1]["outcome"] == "blocked_pending_presentation"
    assert len(h.orders("merchant_a")) + len(h.orders("merchant_b")) == 1
