"""Purchase coordinator: the only component allowed to claim a watch and talk to
the merchant's Complete Checkout (plan §12 steps 7-14, §13, §17, §18).

Guarantees
* The claim is a single transaction: versioned watch update + insert into
  ``purchase_attempts`` under a partial unique index → at most one active claim.
* The outbound Complete Checkout event is persisted *before* it is sent.
* Expiry / cancellation / authorization validity are re-checked at claim time
  and immediately before external submission using the server clock.
* An uncertain outcome (timeout, 5xx, 409) keeps the claim and opens a
  reconciliation case; a claim is never released because a call timed out.
* A presented mandate is not re-presented; a new checkout can only be
  authorised after a rejection receipt (AP2) — enforced via
  ``authorizations.presentation_state``.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy.exc import IntegrityError

from authorization_profiles.base import PreparedPresentation
from common.clock import iso
from common.util import canonical_json, new_id, random_token, sha256_b64url
from persistence import repo
from ucp_adapter import MerchantError, MerchantTimeout
from watch_domain.states import IllegalTransition, WatchStatus, transition

from .context import Context

log = logging.getLogger("pricewatch.coordinator")


class ClaimConflict(Exception):
    pass


class StaleLease(Exception):
    pass


class PurchaseCoordinator:
    def __init__(self, ctx: Context):
        self.ctx = ctx

    # ------------------------------------------------------------- claim
    def claim(self, watch: Dict[str, Any], lease_token: str, run_id: Optional[str], authorization: Dict[str, Any], *, merchant_id: str, checkout_id: str, checkout_payload: Dict[str, Any], total_minor: int, currency: str) -> Dict[str, Any]:
        """Atomically claim the watch for exactly this checkout."""
        now = self.ctx.now()
        attempt_id = new_id("att")
        try:
            with self.ctx.engine.begin() as conn:
                current = repo.get_watch(conn, watch["id"])
                if current is None:
                    raise ClaimConflict("watch vanished")
                if current["lease_token"] != lease_token:
                    raise StaleLease("lease token no longer valid")
                if current["version"] != watch["version"]:
                    raise ClaimConflict("watch version changed (%d → %d)" % (watch["version"], current["version"]))
                if current["cancel_requested"] or current["status"] in (WatchStatus.CANCELLED.value, WatchStatus.EXPIRED.value):
                    raise ClaimConflict("watch cancelled")
                if current["expires_at"] <= now:
                    raise ClaimConflict("watch expired at %s" % iso(current["expires_at"]))
                if current["purchases_completed"] >= current["max_purchases"]:
                    raise ClaimConflict("maximum purchases already reached")
                if authorization["status"] != "active" or authorization["expires_at"] <= now:
                    raise ClaimConflict("authorization not active")
                try:
                    new_status = transition(current["status"], WatchStatus.PURCHASE_CLAIMED.value)
                except IllegalTransition as e:
                    raise ClaimConflict(str(e))
                ok = repo.update_watch_versioned(conn, watch["id"], current["version"], {"status": new_status}, lease_token=lease_token, now=now)
                if not ok:
                    raise ClaimConflict("concurrent update on watch")
                repo.insert_attempt(
                    conn,
                    {
                        "id": attempt_id,
                        "watch_id": watch["id"],
                        "watch_version": current["version"] + 1,
                        "authorization_id": authorization["id"],
                        "evaluation_run_id": run_id,
                        "merchant_id": merchant_id,
                        "checkout_id": checkout_id,
                        "checkout_digest": sha256_b64url(canonical_json(checkout_payload).encode("utf-8")),
                        "final_total_minor": total_minor,
                        "currency": currency,
                        "idempotency_key": "pw-%s-%s" % (watch["id"], random_token(12)),
                        "claim_state": "claimed",
                        "order_state": "none",
                        "payment_state": "none",
                        "external_references": {},
                        "claimed_at": now,
                    },
                )
                if not repo.set_presentation_state(conn, authorization["id"], ["idle", "rejected"], "pending", attempt_id):
                    raise ClaimConflict("authorization has an unresolved presentation; resolve it before presenting again")
                repo.add_event(conn, watch["id"], now, "purchase.claimed", self.ctx.worker_id, {"attempt_id": attempt_id, "merchant_id": merchant_id, "checkout_id": checkout_id, "total_minor": total_minor, "currency": currency}, current["version"] + 1)
                repo.bump_metric(conn, "purchase_attempts")
                attempt = repo.get_attempt(conn, attempt_id)
        except IntegrityError:
            with self.ctx.engine.begin() as conn:
                repo.bump_metric(conn, "duplicate_claims_prevented")
            raise ClaimConflict("another checkout already holds the purchase claim for this watch")
        except ClaimConflict:
            with self.ctx.engine.begin() as conn:
                repo.bump_metric(conn, "duplicate_claims_prevented")
            raise
        return attempt

    # ----------------------------------------------------------- execute
    def execute(self, attempt: Dict[str, Any], lease_token: str, prepared: PreparedPresentation, checkout: Dict[str, Any], merchant: Dict[str, str]) -> Dict[str, Any]:
        ctx = self.ctx
        watch_id = attempt["watch_id"]
        now = ctx.now()
        profile_name = prepared.profile
        auth_id = attempt["authorization_id"]

        # 1. persist the outbound event before anything leaves the process
        event_id = new_id("out")
        with ctx.engine.begin() as conn:
            ctx.vault.store(conn, "%s.prepared_presentation" % profile_name, prepared.native, now, watch_id=watch_id, authorization_id=auth_id, attempt_id=attempt["id"])
            repo.insert_outbound(conn, {"id": event_id, "attempt_id": attempt["id"], "kind": "ucp.complete_checkout", "idempotency_key": attempt["idempotency_key"], "target": "%s:/checkout-sessions/%s/complete" % (attempt["merchant_id"], attempt["checkout_id"]), "payload_digest": "pending", "state": "prepared", "created_at": now})

        # 2. re-check expiry / cancellation / authority before presenting anything
        reason = self._abort_reason(attempt, now)
        if reason:
            return self._abort(attempt, lease_token, reason, presented=False)

        # 3. payment authorization (Credential Provider / Network role)
        cp = ctx.credential_provider.authorize(profile_name, prepared.payment_payload, prepared.checkout_hash, now)
        with ctx.engine.begin() as conn:
            ctx.vault.store(conn, "%s.payment_receipt" % profile_name, {"payment_receipt": cp["payment_receipt"]}, now, watch_id=watch_id, authorization_id=auth_id, attempt_id=attempt["id"])
            repo.add_event(conn, watch_id, now, "payment.authorization", ctx.worker_id, {"attempt_id": attempt["id"], "ok": cp["ok"], "error": cp.get("error"), "error_description": cp.get("error_description")})
        if not cp["ok"]:
            return self._reject(attempt, lease_token, "payment_rejected:%s" % cp.get("error"), payment_state="rejected", receipt_kind="payment")

        handler_id = ctx.ucp.handler_id(attempt["merchant_id"], profile_name)
        complete_payload: Dict[str, Any] = {
            "payment": {"instruments": [{"id": "pi_%s" % attempt["id"], "handler_id": handler_id, "type": "%s_mandate" % profile_name, "credential": {"type": "payment_credential+jwt", "token": cp["credential"]}}]},
        }
        if profile_name == "ap2":
            complete_payload["ap2"] = prepared.merchant_payload
        else:
            complete_payload.update(prepared.merchant_payload)
        payload_digest = sha256_b64url(canonical_json(complete_payload).encode("utf-8"))

        # 4. final server-clock check immediately before external submission
        now = ctx.now()
        reason = self._abort_reason(attempt, now)
        if reason:
            return self._abort(attempt, lease_token, reason, presented=True)

        with ctx.engine.begin() as conn:
            if not repo.update_attempt_state(conn, attempt["id"], ["claimed"], claim_state="submitting", payment_state="authorized", submitted_at=now, external_references={"payment_id": cp["payment_id"], "payment_mandate_reference": cp["reference"]}):
                raise StaleLease("attempt no longer claimed")
            w = repo.get_watch(conn, watch_id)
            if not repo.update_watch_versioned(conn, watch_id, w["version"], {"status": transition(w["status"], WatchStatus.SUBMITTING.value)}, lease_token=lease_token, now=now):
                raise StaleLease("watch changed before submission")
            ctx.vault.store(conn, "ucp.complete_request", complete_payload, now, watch_id=watch_id, authorization_id=auth_id, attempt_id=attempt["id"])
            repo.update_outbound(conn, event_id, payload_digest=payload_digest, state="sent_unknown", sent_at=now)
            repo.add_event(conn, watch_id, now, "purchase.submitting", ctx.worker_id, {"attempt_id": attempt["id"], "idempotency_key": attempt["idempotency_key"], "payload_digest": payload_digest}, w["version"] + 1)

        # 5. external submission under a stable operation identity
        try:
            response = ctx.ucp.complete_checkout(attempt["merchant_id"], attempt["checkout_id"], complete_payload, attempt["idempotency_key"])
        except MerchantTimeout as e:
            return self._uncertain(attempt, event_id, "timeout: %s" % e)
        except MerchantError as e:
            if e.status >= 500 or e.status == 409:
                return self._uncertain(attempt, event_id, "merchant error %d %s" % (e.status, e.code))
            with ctx.engine.begin() as conn:
                repo.update_outbound(conn, event_id, state="failed")
            return self._reject(attempt, lease_token, "merchant_error:%s" % e.code, payment_state="authorized_unused", receipt_kind=None)
        with ctx.engine.begin() as conn:
            repo.update_outbound(conn, event_id, state="acknowledged", acknowledged_at=ctx.now(), response_digest=sha256_b64url(canonical_json(response).encode("utf-8")))
        return self._handle_completion_response(attempt, response, lease_token=lease_token)

    # ---------------------------------------------------------- helpers
    def _abort_reason(self, attempt: Dict[str, Any], now: datetime) -> Optional[str]:
        with self.ctx.engine.begin() as conn:
            w = repo.get_watch(conn, attempt["watch_id"])
            a = repo.active_authorization(conn, attempt["watch_id"])
        if w is None:
            return "watch_missing"
        if w["cancel_requested"] or w["status"] == WatchStatus.CANCELLED.value:
            return "cancelled_before_submission"
        if w["expires_at"] <= now:
            return "deadline_passed_before_submission"
        if a is None or a["id"] != attempt["authorization_id"] or a["expires_at"] <= now or a["status"] != "active":
            return "authorization_expired_before_submission"
        return None

    def _abort(self, attempt: Dict[str, Any], lease_token: str, reason: str, presented: bool) -> Dict[str, Any]:
        """Nothing was submitted to the merchant: release the claim safely."""
        ctx = self.ctx
        now = ctx.now()
        with ctx.engine.begin() as conn:
            repo.update_attempt_state(conn, attempt["id"], ["claimed"], claim_state="aborted", resolved_at=now, last_error=reason, payment_state="authorized_unused" if presented else "none")
            repo.set_presentation_state(conn, attempt["authorization_id"], ["pending"], "rejected" if presented else "idle", None)
            w = repo.get_watch(conn, attempt["watch_id"])
            if reason.startswith("cancelled"):
                new_status = WatchStatus.CANCELLED.value
            elif reason.startswith("deadline"):
                new_status = WatchStatus.EXPIRED.value
            elif reason.startswith("authorization"):
                new_status = WatchStatus.WATCHING.value
            else:
                new_status = WatchStatus.WATCHING.value
            values: Dict[str, Any] = {"status": new_status}
            if new_status == WatchStatus.WATCHING.value:
                values["next_check_at"] = now
            else:
                values.update({"next_check_at": None, "lease_token": None, "lease_owner": None, "lease_expires_at": None})
                values["cancelled_at"] = now if new_status == WatchStatus.CANCELLED.value else None
            repo.update_watch_versioned(conn, w["id"], w["version"], values, lease_token=lease_token, now=now)
            repo.add_event(conn, w["id"], now, "purchase.aborted_before_submission", ctx.worker_id, {"attempt_id": attempt["id"], "reason": reason}, w["version"] + 1)
            if reason.startswith("authorization"):
                repo.add_event(conn, w["id"], now, "authorization.required", ctx.worker_id, {"reason": "authorization expired during checkout preparation; renew consent in the UI"})
                ctx.notifier.notify(conn, watch=w, event="authorization_required", subject="Renewed authorization needed", body="Your authorization expired while a qualifying offer was being prepared. Nothing was purchased.", at=now)
            ctx.ucp.cancel_checkout(attempt["merchant_id"], attempt["checkout_id"])
        if new_status == WatchStatus.WATCHING.value and reason.startswith("authorization"):
            self._pause_for_authorization(attempt["watch_id"], lease_token)
        return {"outcome": "aborted", "reason": reason}

    def _pause_for_authorization(self, watch_id: str, lease_token: Optional[str]) -> None:
        now = self.ctx.now()
        with self.ctx.engine.begin() as conn:
            w = repo.get_watch(conn, watch_id)
            if w and w["status"] == WatchStatus.WATCHING.value:
                repo.update_watch_versioned(conn, watch_id, w["version"], {"status": WatchStatus.PAUSED.value, "next_check_at": None, "lease_token": None, "lease_owner": None, "lease_expires_at": None}, lease_token=lease_token, now=now)
                repo.add_event(conn, watch_id, now, "watch.paused", self.ctx.worker_id, {"reason": "no active authorization"}, w["version"] + 1)

    def _reject(self, attempt: Dict[str, Any], lease_token: str, reason: str, *, payment_state: str, receipt_kind: Optional[str]) -> Dict[str, Any]:
        """A verifier definitively rejected the presentation (rejection receipt held)."""
        ctx = self.ctx
        now = ctx.now()
        with ctx.engine.begin() as conn:
            repo.update_attempt_state(conn, attempt["id"], ["claimed", "submitting"], claim_state="rejected", resolved_at=now, last_error=reason, payment_state=payment_state)
            repo.set_presentation_state(conn, attempt["authorization_id"], ["pending"], "rejected", None)
            a = repo.active_authorization(conn, attempt["watch_id"])
            reusable = a is not None and a["profile"] == "ap2"  # VI: one L3 pair per L2 pair → consumed
            if a is not None and not reusable:
                repo.update_authorization(conn, a["id"], status="consumed")
            w = repo.get_watch(conn, attempt["watch_id"])
            repo.update_watch_versioned(conn, w["id"], w["version"], {"status": transition(w["status"], WatchStatus.WATCHING.value), "next_check_at": now}, lease_token=lease_token, now=now)
            repo.add_event(conn, w["id"], now, "purchase.rejected", ctx.worker_id, {"attempt_id": attempt["id"], "reason": reason, "authorization_reusable": reusable}, w["version"] + 1)
            repo.bump_metric(conn, "purchase_rejections")
            ctx.ucp.cancel_checkout(attempt["merchant_id"], attempt["checkout_id"])
            if not reusable:
                ctx.notifier.notify(conn, watch=w, event="authorization_required", subject="Purchase rejected — new authorization required", body="The merchant or payment verifier rejected the attempt (%s). The single-use authorization is consumed; review and re-authorize to continue watching." % reason, at=now)
        if not reusable:
            self._pause_for_authorization(attempt["watch_id"], lease_token)
        return {"outcome": "rejected", "reason": reason}

    def _uncertain(self, attempt: Dict[str, Any], event_id: Optional[str], reason: str) -> Dict[str, Any]:
        """Outcome unknown: keep the claim, open a reconciliation case."""
        ctx = self.ctx
        now = ctx.now()
        with ctx.engine.begin() as conn:
            repo.update_attempt_state(conn, attempt["id"], ["submitting", "claimed"], claim_state="reconciliation_required", last_error=reason, order_state="unknown")
            w = repo.get_watch(conn, attempt["watch_id"])
            try:
                new_status = transition(w["status"], WatchStatus.RECONCILIATION_REQUIRED.value)
            except IllegalTransition:
                new_status = WatchStatus.RECONCILIATION_REQUIRED.value
            repo.update_watch_versioned(conn, w["id"], w["version"], {"status": new_status, "next_check_at": None, "lease_token": None, "lease_owner": None, "lease_expires_at": None}, now=now)
            if repo.open_case_for_attempt(conn, attempt["id"]) is None:
                repo.insert_case(conn, {"id": new_id("case"), "attempt_id": attempt["id"], "watch_id": w["id"], "state": "open", "summary": reason, "evidence": {"idempotency_key": attempt["idempotency_key"], "checkout_id": attempt["checkout_id"], "merchant_id": attempt["merchant_id"], "outbound_event_id": event_id}, "checks": 0, "opened_at": now})
                repo.bump_metric(conn, "unresolved_executions")
            repo.add_event(conn, w["id"], now, "purchase.outcome_unknown", ctx.worker_id, {"attempt_id": attempt["id"], "reason": reason, "note": "claim retained; order will be reconciled via Get Checkout"}, w["version"] + 1)
            ctx.notifier.notify(conn, watch=w, event="reconciliation_required", subject="Purchase outcome uncertain", body="The merchant did not confirm the order (%s). The watch is locked until the outcome is reconciled; nothing else will be bought." % reason, at=now)
        return {"outcome": "reconciliation_required", "reason": reason}

    def _handle_completion_response(self, attempt: Dict[str, Any], response: Dict[str, Any], *, lease_token: Optional[str], resolution: str = "merchant_confirmed") -> Dict[str, Any]:
        status = response.get("status")
        if status == "completed" and response.get("order"):
            return self._finalize_purchased(attempt, response, lease_token=lease_token, resolution=resolution)
        if status == "complete_in_progress":
            return self._uncertain(attempt, None, "merchant accepted completion asynchronously (complete_in_progress)")
        errors = [m for m in response.get("messages", []) or [] if m.get("type") == "error"]
        code = errors[0].get("code") if errors else "not_completed:%s" % status
        return self._reject(attempt, lease_token or "", "merchant_rejected:%s" % code, payment_state="authorized_unused", receipt_kind="checkout") if lease_token is not None else self._resolve_not_purchased(attempt, "merchant_rejected:%s" % code)

    def _finalize_purchased(self, attempt: Dict[str, Any], response: Dict[str, Any], *, lease_token: Optional[str], resolution: str) -> Dict[str, Any]:
        ctx = self.ctx
        now = ctx.now()
        order = response["order"]
        with ctx.engine.begin() as conn:
            latest = repo.get_attempt(conn, attempt["id"]) or attempt
            refs = dict(latest.get("external_references") or {})
            refs.update({"order_id": order["id"], "permalink_url": order.get("permalink_url"), "checkout_id": attempt["checkout_id"], "merchant_id": attempt["merchant_id"]})
            if (response.get("ap2") or {}).get("checkout_receipt"):
                refs["checkout_receipt_present"] = True
            if not repo.update_attempt_state(conn, attempt["id"], ["submitting", "reconciliation_required", "claimed"], claim_state="purchased", order_state="placed", payment_state="captured", resolved_at=now, external_references=refs):
                return {"outcome": "noop", "reason": "attempt already resolved"}
            ctx.vault.store(conn, "ucp.completed_checkout", response, now, watch_id=attempt["watch_id"], authorization_id=attempt["authorization_id"], attempt_id=attempt["id"])
            receipt = (response.get("ap2") or {}).get("checkout_receipt")
            if receipt:
                ctx.vault.store(conn, "ap2.checkout_receipt", {"checkout_receipt": receipt}, now, watch_id=attempt["watch_id"], authorization_id=attempt["authorization_id"], attempt_id=attempt["id"])
            if response.get("vi"):
                ctx.vault.store(conn, "vi.merchant_result", response["vi"], now, watch_id=attempt["watch_id"], authorization_id=attempt["authorization_id"], attempt_id=attempt["id"])
            w = repo.get_watch(conn, attempt["watch_id"])
            repo.update_watch_versioned(conn, w["id"], w["version"], {"status": WatchStatus.PURCHASED.value, "purchases_completed": w["purchases_completed"] + 1, "next_check_at": None, "lease_token": None, "lease_owner": None, "lease_expires_at": None}, now=now)
            repo.update_authorization(conn, attempt["authorization_id"], status="consumed")
            repo.set_presentation_state(conn, attempt["authorization_id"], ["pending", "idle", "rejected"], "accepted", attempt["id"])
            case = repo.open_case_for_attempt(conn, attempt["id"])
            if case:
                repo.update_case(conn, case["id"], state="resolved", resolved_at=now, resolution="order_found")
            repo.add_event(conn, w["id"], now, "purchase.completed", ctx.worker_id, {"attempt_id": attempt["id"], "order_id": order["id"], "permalink_url": order.get("permalink_url"), "total_minor": attempt["final_total_minor"], "currency": attempt["currency"], "resolution": resolution}, w["version"] + 1)
            repo.bump_metric(conn, "purchases_completed")
            ctx.notifier.notify(conn, watch=w, event="purchased", subject="Purchase completed", body="Order %s placed with %s for %d %s (delivered total)." % (order["id"], attempt["merchant_id"], attempt["final_total_minor"], attempt["currency"]), at=now)
        return {"outcome": "purchased", "order_id": order["id"]}

    def _resolve_not_purchased(self, attempt: Dict[str, Any], reason: str) -> Dict[str, Any]:
        ctx = self.ctx
        now = ctx.now()
        with ctx.engine.begin() as conn:
            repo.update_attempt_state(conn, attempt["id"], ["reconciliation_required", "submitting"], claim_state="resolved_not_purchased", order_state="not_placed", resolved_at=now, last_error=reason)
            repo.set_presentation_state(conn, attempt["authorization_id"], ["pending"], "rejected", None)
            repo.update_authorization(conn, attempt["authorization_id"], status="consumed")
            w = repo.get_watch(conn, attempt["watch_id"])
            repo.update_watch_versioned(conn, w["id"], w["version"], {"status": WatchStatus.RESOLVED_NOT_PURCHASED.value, "next_check_at": None}, now=now)
            case = repo.open_case_for_attempt(conn, attempt["id"])
            if case:
                repo.update_case(conn, case["id"], state="resolved", resolved_at=now, resolution="no_order")
            repo.add_event(conn, w["id"], now, "purchase.resolved_not_purchased", ctx.worker_id, {"attempt_id": attempt["id"], "reason": reason, "note": "authority is not reactivated automatically; new consent is required to continue"}, w["version"] + 1)
            ctx.notifier.notify(conn, watch=w, event="resolved_not_purchased", subject="No order was placed", body="The uncertain attempt was resolved: the merchant did not place an order (%s). Re-authorize to keep watching." % reason, at=now)
        return {"outcome": "resolved_not_purchased", "reason": reason}

    # ------------------------------------------------------- reconcile
    def reconcile(self, attempt: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve an uncertain attempt using the merchant's authoritative state."""
        ctx = self.ctx
        now = ctx.now()
        with ctx.engine.begin() as conn:
            case = repo.open_case_for_attempt(conn, attempt["id"])
            if case is None:
                repo.insert_case(conn, {"id": new_id("case"), "attempt_id": attempt["id"], "watch_id": attempt["watch_id"], "state": "open", "summary": attempt.get("last_error") or "recovered after restart", "evidence": {"idempotency_key": attempt["idempotency_key"], "checkout_id": attempt["checkout_id"], "merchant_id": attempt["merchant_id"]}, "checks": 0, "opened_at": now})
                case = repo.open_case_for_attempt(conn, attempt["id"])
            repo.update_case(conn, case["id"], checks=case["checks"] + 1)
        try:
            checkout = ctx.ucp.get_checkout(attempt["merchant_id"], attempt["checkout_id"])
        except (MerchantTimeout, MerchantError) as e:
            with ctx.engine.begin() as conn:
                repo.add_event(conn, attempt["watch_id"], now, "reconciliation.check_failed", ctx.worker_id, {"attempt_id": attempt["id"], "error": str(e)})
            return {"outcome": "still_unknown", "reason": str(e)}
        with ctx.engine.begin() as conn:
            repo.add_event(conn, attempt["watch_id"], now, "reconciliation.checked", ctx.worker_id, {"attempt_id": attempt["id"], "checkout_status": checkout.get("status"), "order_id": (checkout.get("order") or {}).get("id")})
        status = checkout.get("status")
        if status == "completed" and checkout.get("order"):
            return self._finalize_purchased(attempt, checkout, lease_token=None, resolution="recovered_via_get_checkout")
        if status == "canceled":
            return self._resolve_not_purchased(attempt, "checkout canceled/expired at merchant; no order")
        if status == "complete_in_progress":
            return {"outcome": "still_unknown", "reason": "merchant still processing"}
        if status == "ready_for_complete":
            # Get Checkout established the request was never applied: UCP permits resubmitting the
            # identical request under the same idempotency key.
            with ctx.engine.begin() as conn:
                arts = ctx.vault.load_many(conn, attempt_id=attempt["id"])
            payload = next((a["content"] for a in arts.values() if a["kind"] == "ucp.complete_request"), None)
            if payload is None:
                return self._resolve_not_purchased(attempt, "original request unavailable; not retried")
            try:
                response = ctx.ucp.complete_checkout(attempt["merchant_id"], attempt["checkout_id"], payload, attempt["idempotency_key"])
            except (MerchantTimeout, MerchantError) as e:
                return {"outcome": "still_unknown", "reason": "resubmission unresolved: %s" % e}
            with ctx.engine.begin() as conn:
                repo.add_event(conn, attempt["watch_id"], ctx.now(), "reconciliation.resubmitted_identical_request", ctx.worker_id, {"attempt_id": attempt["id"], "idempotency_key": attempt["idempotency_key"]})
            return self._handle_completion_response(attempt, response, lease_token=None, resolution="resubmitted_same_idempotency_key")
        return {"outcome": "still_unknown", "reason": "unexpected checkout status %s" % status}

    def reconcile_open(self) -> int:
        n = 0
        with self.ctx.engine.begin() as conn:
            attempts = repo.attempts_in_states(conn, ["reconciliation_required"])
        for a in attempts:
            self.reconcile(a)
            n += 1
        return n

    # ---------------------------------------------------------- recover
    def recover(self) -> Dict[str, int]:
        """Called when a worker (re)starts: never buy twice, never lose a claim."""
        ctx = self.ctx
        summary = {"reconciled": 0, "aborted": 0}
        with ctx.engine.begin() as conn:
            attempts = repo.attempts_in_states(conn, ["claimed", "submitting", "reconciliation_required"])
        for a in attempts:
            with ctx.engine.begin() as conn:
                outbound = repo.outbound_for_attempt(conn, a["id"])
            sent = any(o["state"] in ("sent_unknown", "acknowledged") for o in outbound)
            if a["claim_state"] == "claimed" and not sent:
                # Nothing left the process: release the claim and resume watching.
                now = ctx.now()
                with ctx.engine.begin() as conn:
                    repo.update_attempt_state(conn, a["id"], ["claimed"], claim_state="aborted", resolved_at=now, last_error="worker restarted before submission")
                    repo.set_presentation_state(conn, a["authorization_id"], ["pending"], "idle", None)
                    w = repo.get_watch(conn, a["watch_id"])
                    if w and w["status"] in (WatchStatus.PURCHASE_CLAIMED.value, WatchStatus.CHECKOUT_VALIDATING.value, WatchStatus.CANDIDATE_FOUND.value):
                        repo.update_watch_versioned(conn, w["id"], w["version"], {"status": WatchStatus.WATCHING.value, "next_check_at": now, "lease_token": None, "lease_owner": None, "lease_expires_at": None}, now=now)
                    repo.add_event(conn, a["watch_id"], now, "recovery.claim_released", ctx.worker_id, {"attempt_id": a["id"], "reason": "no outbound submission recorded"})
                ctx.ucp.cancel_checkout(a["merchant_id"], a["checkout_id"])
                summary["aborted"] += 1
            else:
                self.reconcile(a)
                summary["reconciled"] += 1
        # Stale leases held by dead workers simply expire; nothing to do here.
        return summary
