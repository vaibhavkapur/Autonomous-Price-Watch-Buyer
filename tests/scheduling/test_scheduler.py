"""Durable scheduling: leases, duplicate messages, expiry, restarts, backoff."""
from datetime import timedelta

import pytest

from harness import DEADLINE
from persistence import repo
from purchase_coordinator import ClaimConflict, PurchaseCoordinator, StaleLease


def eligible(h, merchant="merchant_a", sku="K1-BLK-US"):
    h.set_price(merchant, sku, item_price_minor=8900, shipping_minor=500, tax_minor=400)


def test_watch_is_polled_on_cadence_and_rescheduled_from_now(harness):
    h = harness
    w = h.create_active_watch()
    assert h.watch(w["id"])["next_check_at"] == h.clock.now()
    r = h.tick()
    assert r[0]["outcome"] == "no_candidate"
    w = h.watch(w["id"])
    assert w["next_check_at"] == h.clock.now() + timedelta(seconds=60)
    assert w["lease_token"] is None and w["schedule_version"] == 2
    # not due yet → nothing happens
    h.advance(seconds=30)
    assert h.tick() == []
    # downtime: 10 hours pass; exactly one catch-up evaluation, then normal cadence from *now*
    h.advance(hours=10)
    r = h.tick()
    assert len(r) == 1 and r[0]["outcome"] == "no_candidate"
    assert h.watch(w["id"])["next_check_at"] == h.clock.now() + timedelta(seconds=60)
    assert h.tick() == []


def test_duplicate_scheduler_messages_are_harmless(harness):
    h = harness
    w = h.create_active_watch()
    eligible(h)
    # two "messages" for the same watch: the second finds nothing to do
    r1 = h.tick()
    r2 = h.tick()
    assert r1[0]["outcome"] == "purchased" and r2 == []
    assert len(h.orders("merchant_a")) == 1
    # a stray evaluate-now on a purchased watch is ignored: not schedulable
    version = h.watch(w["id"])["version"]
    with h.engine.begin() as conn:
        repo.update_watch_versioned(conn, w["id"], version, {"next_check_at": h.clock.now()}, bump_version=False)
    assert h.tick() == []
    assert len(h.orders("merchant_a")) == 1


def test_only_one_worker_acquires_the_lease(harness):
    h = harness
    w = h.create_active_watch()
    other = h.new_worker("worker-2")
    other.start()
    claimed_1 = h.ctx.scheduler.claim_due()
    claimed_2 = other.ctx.scheduler.claim_due()
    assert len(claimed_1) == 1 and claimed_2 == []
    # the lease expires → a second worker may take over
    h.advance(seconds=h.settings.lease_seconds + 1)
    claimed_2 = other.ctx.scheduler.claim_due()
    assert len(claimed_2) == 1 and claimed_2[0][1] != claimed_1[0][1]


def test_stale_worker_lease_cannot_claim_or_submit(harness):
    h = harness
    w = h.create_active_watch()
    (watch, old_token), = h.ctx.scheduler.claim_due()
    h.advance(seconds=h.settings.lease_seconds + 1)
    other = h.new_worker("worker-2")
    other.start()
    (watch2, new_token), = other.ctx.scheduler.claim_due()
    with h.engine.begin() as conn:
        auth = repo.active_authorization(conn, w["id"])
    coord = PurchaseCoordinator(h.ctx)
    with pytest.raises(StaleLease):
        coord.claim(watch, old_token, None, auth, merchant_id="merchant_a", checkout_id="chk", checkout_payload={}, total_minor=9800, currency="USD")
    # and the stale worker's reschedule is a no-op
    with h.engine.begin() as conn:
        assert h.ctx.scheduler.reschedule(conn, watch, old_token) is None
    assert h.watch(w["id"])["lease_token"] == new_token
    with h.engine.begin() as conn:
        assert repo.all_metrics(conn).get("duplicate_claims_prevented", 0) >= 0


def test_expiry_is_decided_by_the_server_clock(harness):
    h = harness
    w = h.create_active_watch()
    eligible(h)
    # the check was scheduled before the deadline but runs after it → no purchase
    h.clock.set(DEADLINE + timedelta(seconds=1))
    r = h.tick()
    assert r[0]["outcome"] == "expired"
    w = h.watch(w["id"])
    assert w["status"] == "expired" and w["next_check_at"] is None
    assert h.orders("merchant_a") == []
    with h.engine.begin() as conn:
        assert repo.list_authorizations(conn, w["id"])[0]["status"] == "expired"
        assert any(n["dedupe_key"].endswith(":expired") for n in repo.list_notifications(conn, w["id"]))
    assert h.tick() == []


def test_final_check_is_clamped_to_the_deadline(harness):
    h = harness
    w = h.create_active_watch(poll_interval_seconds=3600)
    h.clock.set(DEADLINE - timedelta(minutes=10))
    h.tick()
    assert h.watch(w["id"])["next_check_at"] == DEADLINE


def test_merchant_errors_back_off_without_blocking_the_other_merchant(harness):
    h = harness
    w = h.create_active_watch()
    h.merchant_clients["merchant_a"].close()  # simulate an unreachable merchant

    class Boom:
        def request(self, *a, **k):
            import httpx

            raise httpx.ConnectError("down")

    h.ctx.ucp.endpoints["merchant_a"].client = Boom()
    r = h.tick()
    assert r[0]["outcome"] == "no_candidate" and r[0]["all_merchants_failed"] is False
    with h.engine.begin() as conn:
        health = repo.get_merchant_health(conn, "merchant_a")
        obs = repo.list_observations(conn, w["id"])
    assert health["consecutive_errors"] == 1 and health["backoff_until"] > h.clock.now()
    assert {o["merchant_id"]: o["eligibility"] for o in obs} == {"merchant_a": "error", "merchant_b": "rejected"}
    # while backing off, merchant_a is skipped (rate limit reason) and merchant_b still polled
    h.advance(seconds=61)
    h.tick()
    with h.engine.begin() as conn:
        latest = repo.list_observations(conn, w["id"])[:2]
    assert any("merchant_backoff_until" in "".join(o["reasons"]) for o in latest if o["merchant_id"] == "merchant_a")


def test_all_merchants_failing_increases_watch_backoff(harness):
    h = harness
    w = h.create_active_watch()

    class Boom:
        def request(self, *a, **k):
            import httpx

            raise httpx.ConnectError("down")

    for ep in h.ctx.ucp.endpoints.values():
        ep.client = Boom()
    h.tick()
    w1 = h.watch(w["id"])
    assert w1["consecutive_errors"] == 1 and w1["next_check_at"] == h.clock.now() + timedelta(seconds=120)


def test_pause_resume_cancel_by_user(harness):
    h = harness
    w = h.create_active_watch()
    w = h.service.pause(w, w["version"])
    assert w["status"] == "paused" and w["next_check_at"] is None
    assert h.tick() == []
    w = h.service.resume(w, w["version"])
    assert w["status"] == "watching"
    w = h.service.cancel(w, w["version"])
    assert w["status"] == "cancelled" and w["cancel_requested"]
    eligible(h)
    assert h.tick() == [] and h.orders("merchant_a") == []
    with h.engine.begin() as conn:
        assert repo.list_authorizations(conn, w["id"])[0]["status"] == "revoked"


def test_user_update_during_lease_invalidates_worker_version(harness):
    h = harness
    w = h.create_active_watch()
    (watch, token), = h.ctx.scheduler.claim_due()
    h.service.pause(h.watch(w["id"]), h.watch(w["id"])["version"])
    with h.engine.begin() as conn:
        auth = repo.active_authorization(conn, w["id"])
    with pytest.raises(ClaimConflict):
        PurchaseCoordinator(h.ctx).claim(watch, token, None, auth, merchant_id="merchant_a", checkout_id="chk", checkout_payload={}, total_minor=9800, currency="USD")
