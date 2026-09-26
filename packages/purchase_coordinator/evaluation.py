"""One evaluation run for a leased watch (plan §12 steps 1-8).

Deterministic, LLM-free: collect offers → discard stale/incomplete/wrong
product → rank → fresh UCP checkout → validate every final field → prepare and
self-verify authorization evidence → hand the claim to the coordinator.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from authorization_profiles.base import ProfileError
from common.clock import iso
from common.util import new_id, random_token
from offer_evaluation import Offer, PriceComponents, Verdict, evaluate_offer, offer_from_feed, rank, validate_checkout
from persistence import repo
from ucp_adapter import MerchantError, MerchantTimeout
from watch_domain.states import WatchStatus, transition

from .context import Context
from .coordinator import ClaimConflict, PurchaseCoordinator, StaleLease

log = logging.getLogger("pricewatch.evaluation")


def _set_status(ctx: Context, watch_id: str, lease_token: str, new_status: str, event: str, details: Dict[str, Any]) -> Dict[str, Any]:
    now = ctx.now()
    with ctx.engine.begin() as conn:
        w = repo.get_watch(conn, watch_id)
        if w is None or w["lease_token"] != lease_token:
            raise StaleLease("lease lost while moving to %s" % new_status)
        status = transition(w["status"], new_status)
        values: Dict[str, Any] = {"status": status}
        if status in (WatchStatus.EXPIRED.value, WatchStatus.CANCELLED.value):
            values.update({"next_check_at": None, "lease_token": None, "lease_owner": None, "lease_expires_at": None})
            if status == WatchStatus.CANCELLED.value:
                values["cancelled_at"] = now
        if not repo.update_watch_versioned(conn, watch_id, w["version"], values, lease_token=lease_token, now=now):
            raise StaleLease("version changed while moving to %s" % new_status)
        repo.add_event(conn, watch_id, now, event, ctx.worker_id, details, w["version"] + 1)
        return repo.get_watch(conn, watch_id)


def evaluate_watch(ctx: Context, watch: Dict[str, Any], lease_token: str) -> Dict[str, Any]:
    now = ctx.now()
    run_id = new_id("run")
    with ctx.engine.begin() as conn:
        repo.insert_run(conn, {"id": run_id, "watch_id": watch["id"], "watch_version": watch["version"], "lease_token": lease_token, "worker_id": ctx.worker_id, "started_at": now})
        repo.bump_metric(conn, "evaluation_runs")
    try:
        outcome = _evaluate(ctx, watch, lease_token, run_id)
    except StaleLease as e:
        outcome = {"outcome": "stale_lease", "reason": str(e)}
        with ctx.engine.begin() as conn:
            repo.bump_metric(conn, "stale_lease_rejections")
    finally:
        pass
    with ctx.engine.begin() as conn:
        repo.finish_run(conn, run_id, ctx.now(), outcome.get("outcome", "unknown"), outcome, outcome.get("selected_offer_id"))
        # Reschedule only if the watch is still watching and we still hold the lease.
        if outcome.get("outcome") not in ("purchased", "reconciliation_required"):
            ctx.scheduler.reschedule(conn, watch, lease_token, error=bool(outcome.get("all_merchants_failed")))
    return outcome


def _evaluate(ctx: Context, watch: Dict[str, Any], lease_token: str, run_id: str) -> Dict[str, Any]:
    now = ctx.now()
    wid = watch["id"]

    # --- explicit expiry / cancellation checks with the server clock
    if watch["expires_at"] <= now:
        _set_status(ctx, wid, lease_token, WatchStatus.EXPIRED.value, "watch.expired", {"checked_at": iso(now)})
        with ctx.engine.begin() as conn:
            for a in repo.list_authorizations(conn, wid):
                if a["status"] == "active":
                    repo.update_authorization(conn, a["id"], status="expired")
            ctx.notifier.notify(conn, watch=watch, event="expired", subject="Watch expired without a purchase", body="The deadline passed before a qualifying delivered price appeared.", at=now)
        return {"outcome": "expired"}
    if watch["cancel_requested"]:
        _set_status(ctx, wid, lease_token, WatchStatus.CANCELLED.value, "watch.cancelled", {"by": "user", "observed_by_worker": True})
        return {"outcome": "cancelled"}

    with ctx.engine.begin() as conn:
        auth = repo.active_authorization(conn, wid)
        dest = repo.get_destination(conn, watch["destination_id"]) if watch["destination_id"] else None
    if auth is None or auth["expires_at"] <= now or auth["watch_version"] > watch["version"]:
        _set_status(ctx, wid, lease_token, WatchStatus.PAUSED.value, "watch.paused", {"reason": "no active authorization for this watch version"})
        with ctx.engine.begin() as conn:
            repo.add_event(conn, wid, now, "authorization.required", ctx.worker_id, {"reason": "expired or missing"})
            ctx.notifier.notify(conn, watch=watch, event="authorization_required", subject="Authorization needed", body="The watch is paused until you renew the purchase authorization.", at=now)
        return {"outcome": "paused_authorization_required"}
    if dest is None:
        _set_status(ctx, wid, lease_token, WatchStatus.PAUSED.value, "watch.paused", {"reason": "destination missing; delivered price cannot be computed"})
        return {"outcome": "paused_destination_missing"}
    if auth["presentation_state"] == "pending":
        return {"outcome": "blocked_pending_presentation", "reason": "a previous presentation has no receipt yet"}

    # --- step 2/3: collect + evaluate offers from permitted merchants only
    observations: List[Tuple[Offer, Verdict, str]] = []
    successes = 0
    for merchant_id in watch["allowed_merchants"]:
        sku = watch["merchant_sku_mapping"].get(merchant_id)
        with ctx.engine.begin() as conn:
            allowed, why = ctx.scheduler.merchant_allowed(conn, merchant_id)
        obs_id = new_id("obs")
        if sku is None:
            offer = Offer(merchant_id, None, None, {}, None, None, PriceComponents(None, None, None, source="none"), now, None, None, "config", error="no_sku_mapping")
        elif not allowed:
            offer = Offer(merchant_id, None, sku, {}, None, None, PriceComponents(None, None, None, source="none"), now, None, None, "rate_limit", error=why)
        else:
            try:
                quote = ctx.ucp.feed_quote(merchant_id, sku, watch["quantity"], dest["address"])
                offer = offer_from_feed(merchant_id, quote, ctx.now(), "%s:/app-feed/offers?sku=%s" % (merchant_id, sku))
                successes += 1
                with ctx.engine.begin() as conn:
                    ctx.scheduler.merchant_result(conn, merchant_id, True)
            except (MerchantError, MerchantTimeout) as e:
                offer = Offer(merchant_id, None, sku, {}, None, None, PriceComponents(None, None, None, source="none"), now, None, None, "%s:/app-feed/offers" % merchant_id, error=str(e))
                with ctx.engine.begin() as conn:
                    ctx.scheduler.merchant_result(conn, merchant_id, False, str(e))
        verdict = evaluate_offer(
            offer,
            expected_sku=sku or "",
            quantity=watch["quantity"],
            currency=watch["currency"],
            operator=watch["price_operator"],
            threshold_minor=watch["threshold_minor"],
            allowed_merchants=watch["allowed_merchants"],
            now=ctx.now(),
            freshness=ctx.freshness,
        )
        observations.append((offer, verdict, obs_id))
        with ctx.engine.begin() as conn:
            repo.insert_observation(
                conn,
                {
                    "id": obs_id,
                    "watch_id": wid,
                    "evaluation_run_id": run_id,
                    "merchant_id": merchant_id,
                    "native_offer_id": offer.native_offer_id,
                    "sku": offer.sku,
                    "variant": offer.variant,
                    "quantity_available": offer.quantity_available,
                    "price_components": offer.components.as_dict(),
                    "total_minor": verdict.total_minor,
                    "currency": offer.currency,
                    "observed_at": offer.observed_at,
                    "valid_until": offer.valid_until,
                    "delivery_estimate": offer.delivery_estimate,
                    "source_reference": offer.source_reference,
                    "eligibility": verdict.eligibility,
                    "reasons": verdict.reasons,
                },
            )
            repo.bump_metric(conn, "observations")
            repo.bump_metric(conn, "observations_%s" % verdict.eligibility)

    eligible = [(o, v) for (o, v, _) in observations if v.eligibility == "eligible"]
    ranked = rank(eligible)
    obs_by_offer = {id(o): oid for (o, _, oid) in observations}
    if not ranked:
        return {"outcome": "no_candidate", "observations": len(observations), "all_merchants_failed": successes == 0}

    # --- step 4/5: candidate → fresh checkout with the selected merchant
    _set_status(ctx, wid, lease_token, WatchStatus.CANDIDATE_FOUND.value, "candidate.found", {"ranking": [{"merchant_id": o.merchant_id, "total_minor": v.total_minor, "observation_id": obs_by_offer[id(o)]} for o, v in ranked]})
    with ctx.engine.begin() as conn:
        repo.bump_metric(conn, "qualifying_offers", len(ranked))
        user_row = repo.get_user(conn, watch["user_id"])
    buyer = {"full_name": user_row["name"] if user_row else watch["user_id"], "email": "%s@example.invalid" % watch["user_id"]}
    coordinator = PurchaseCoordinator(ctx)
    rejected_at_checkout: List[Dict[str, Any]] = []
    moved_to_validating = False

    for offer, verdict in ranked:
        merchant_id = offer.merchant_id
        sku = watch["merchant_sku_mapping"][merchant_id]
        try:
            merchant = ctx.ucp.merchant_descriptor(merchant_id)
            checkout = ctx.ucp.create_checkout(merchant_id, sku, watch["quantity"], buyer, dest["address"], idempotency_key="pw-%s-%s" % (run_id, merchant_id))
            signed = ctx.ucp.verify_checkout_jwt(merchant_id, checkout)
        except (MerchantError, MerchantTimeout) as e:
            rejected_at_checkout.append({"merchant_id": merchant_id, "reasons": ["checkout_failed:%s" % e]})
            with ctx.engine.begin() as conn:
                repo.add_event(conn, wid, ctx.now(), "checkout.failed", ctx.worker_id, {"merchant_id": merchant_id, "error": str(e)})
                ctx.scheduler.merchant_result(conn, merchant_id, False, str(e))
            continue
        if not moved_to_validating:
            _set_status(ctx, wid, lease_token, WatchStatus.CHECKOUT_VALIDATING.value, "checkout.created", {"merchant_id": merchant_id, "checkout_id": checkout["id"], "checkout_jwt_kid": "verified"})
            moved_to_validating = True
        now = ctx.now()
        cv = validate_checkout(signed, merchant_id=merchant_id, expected_sku=sku, quantity=watch["quantity"], currency=watch["currency"], operator=watch["price_operator"], threshold_minor=watch["threshold_minor"], now=now)
        with ctx.engine.begin() as conn:
            repo.insert_observation(
                conn,
                {
                    "id": new_id("obs"), "watch_id": wid, "evaluation_run_id": run_id, "merchant_id": merchant_id, "native_offer_id": checkout["id"], "sku": sku,
                    "variant": offer.variant, "quantity_available": offer.quantity_available,
                    "price_components": _components_from_totals(signed.get("totals", []), checkout["id"], now), "total_minor": cv.total_minor, "currency": signed.get("currency"),
                    "observed_at": now, "valid_until": None, "delivery_estimate": offer.delivery_estimate, "source_reference": "ucp_checkout:%s" % checkout["id"],
                    "eligibility": cv.eligibility if cv.eligibility != "eligible" else "eligible", "reasons": cv.reasons,
                },
            )
            repo.add_event(conn, wid, now, "checkout.validated" if cv.eligibility == "eligible" else "checkout.rejected", ctx.worker_id, {"merchant_id": merchant_id, "checkout_id": checkout["id"], "total_minor": cv.total_minor, "feed_total_minor": verdict.total_minor, "reasons": cv.reasons})
        if cv.eligibility != "eligible":
            rejected_at_checkout.append({"merchant_id": merchant_id, "checkout_id": checkout["id"], "reasons": cv.reasons})
            ctx.ucp.cancel_checkout(merchant_id, checkout["id"])
            continue

        # --- step 7: transaction-specific authorization evidence + self-verification
        profile = ctx.profile(auth["profile"])
        with ctx.engine.begin() as conn:
            consent = ctx.vault.load(conn, auth["artifact_reference"])
        checkout_jwt = checkout["ap2"]["checkout_jwt"]
        try:
            prepared = profile.prepare(consent, checkout_jwt, signed, merchant, cv.total_minor, watch["currency"], now, merchant["website"])
        except ProfileError as e:
            rejected_at_checkout.append({"merchant_id": merchant_id, "checkout_id": checkout["id"], "reasons": ["authorization_scope:%s" % e]})
            ctx.ucp.cancel_checkout(merchant_id, checkout["id"])
            continue
        self_check = profile.verify_for_merchant(prepared.merchant_payload, checkout_jwt, merchant, now)
        with ctx.engine.begin() as conn:
            repo.add_event(conn, wid, now, "authorization.prepared", ctx.worker_id, {"profile": auth["profile"], "profile_version": auth["profile_version"], "checkout_hash": prepared.checkout_hash, "reference": prepared.reference, "self_verification": {"ok": self_check.ok, "code": self_check.code, "description": self_check.description}})
        if not self_check.ok:
            rejected_at_checkout.append({"merchant_id": merchant_id, "checkout_id": checkout["id"], "reasons": ["self_verification_failed:%s" % self_check.code]})
            ctx.ucp.cancel_checkout(merchant_id, checkout["id"])
            continue

        # --- step 8: atomic claim, then execution
        fresh_watch = _current(ctx, wid)
        try:
            attempt = coordinator.claim(fresh_watch, lease_token, run_id, auth, merchant_id=merchant_id, checkout_id=checkout["id"], checkout_payload=signed, total_minor=cv.total_minor, currency=watch["currency"])
        except ClaimConflict as e:
            ctx.ucp.cancel_checkout(merchant_id, checkout["id"])
            with ctx.engine.begin() as conn:
                repo.add_event(conn, wid, ctx.now(), "purchase.claim_conflict", ctx.worker_id, {"merchant_id": merchant_id, "checkout_id": checkout["id"], "reason": str(e)})
            return {"outcome": "claim_conflict", "reason": str(e), "selected_offer_id": obs_by_offer[id(offer)]}
        result = coordinator.execute(attempt, lease_token, prepared, signed, merchant)
        result["selected_offer_id"] = obs_by_offer[id(offer)]
        result["attempt_id"] = attempt["id"]
        return result

    # every candidate failed at checkout → resume watching
    _set_status(ctx, wid, lease_token, WatchStatus.WATCHING.value, "candidate.rejected_at_checkout", {"rejected": rejected_at_checkout})
    return {"outcome": "candidates_rejected_at_checkout", "rejected": rejected_at_checkout}


def _current(ctx: Context, watch_id: str) -> Dict[str, Any]:
    with ctx.engine.begin() as conn:
        return repo.get_watch(conn, watch_id)


def _components_from_totals(totals: List[Dict[str, Any]], checkout_id: str, now) -> Dict[str, Any]:
    from offer_evaluation import totals_to_components

    return totals_to_components(totals, "ucp_checkout:%s" % checkout_id, iso(now)).as_dict()
