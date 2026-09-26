"""Watch lifecycle service used by the API (plan §7, §13, §15, §16)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from authorization_profiles.base import ConsentRequest
from common.clock import iso
from common.money import inclusive_ceiling
from common.util import new_id
from persistence import repo
from product_identity import catalog_item, load_merchant_catalog, load_products, verify_mapping
from purchase_coordinator.context import Context
from watch_domain.models import WatchDraft, normalize_rule
from watch_domain.states import ACTIVE_STATUSES, IN_FLIGHT_STATUSES, IllegalTransition, WatchStatus, transition


class ServiceError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class WatchService:
    def __init__(self, ctx: Context, merchant_catalogs: Optional[Dict[str, Dict[str, Any]]] = None):
        self.ctx = ctx
        self.catalogs = merchant_catalogs or {m: load_merchant_catalog(m) for m in ctx.settings.merchant_base_urls}
        self.products = load_products()

    # ------------------------------------------------------------ create
    def create(self, user_id: str, draft: WatchDraft) -> Dict[str, Any]:
        now = self.ctx.now()
        product = self.products.get(draft.product.product_id)
        if product is None:
            raise ServiceError(422, "unknown_product", "product %s is not in the catalog" % draft.product.product_id)
        for m in draft.allowed_merchants:
            if m not in self.catalogs:
                raise ServiceError(422, "unknown_merchant", "merchant %s is not configured" % m)
            sku = draft.merchant_skus.get(m)
            ok, reasons = verify_mapping(product, catalog_item(self.catalogs[m], sku) if sku else None)
            if not ok:
                raise ServiceError(422, "sku_mapping_rejected", "merchant %s sku %s: %s" % (m, sku, "; ".join(reasons)))
        if draft.expires_at <= now:
            raise ServiceError(422, "deadline_in_past", "expires_at must be in the future")
        if draft.destination_id:
            with self.ctx.engine.begin() as conn:
                dest = repo.get_destination(conn, draft.destination_id)
            if dest is None or dest["user_id"] != user_id:
                raise ServiceError(422, "unknown_destination", "destination not found for this user")
        watch_id = new_id("watch")
        with self.ctx.engine.begin() as conn:
            repo.insert_watch(
                conn,
                {
                    "id": watch_id, "user_id": user_id, "agent_id": self.ctx.settings.agent_id, "version": 1, "status": WatchStatus.DRAFT.value,
                    "original_request": draft.original_request,
                    "product_constraints": {"product_id": product["product_id"], "title": product["title"], "attributes": product.get("attributes", {}), "bundle": product.get("bundle", False)},
                    "merchant_sku_mapping": {m: draft.merchant_skus[m] for m in draft.allowed_merchants},
                    "price_operator": draft.price_rule.operator, "threshold_minor": draft.price_rule.delivered_total_minor, "currency": draft.currency,
                    "quantity": draft.quantity, "destination_id": draft.destination_id, "allowed_merchants": list(draft.allowed_merchants),
                    "max_purchases": draft.max_purchases, "purchases_completed": 0, "authorization_profile": draft.authorization_profile,
                    "expires_at": draft.expires_at, "timezone": draft.timezone, "poll_interval_seconds": draft.poll_interval_seconds, "schedule_version": 1,
                    "next_check_at": None, "consecutive_errors": 0, "created_at": now, "updated_at": now, "cancel_requested": False,
                },
            )
            repo.add_event(conn, watch_id, now, "watch.created", user_id, {"draft": draft.model_dump(mode="json")}, 1)
            return repo.get_watch(conn, watch_id)

    # ---------------------------------------------------------- proposal
    def proposal(self, watch: Dict[str, Any]) -> Dict[str, Any]:
        """The Trusted Surface content: the actual conditions being delegated."""
        draft = self._draft_from_watch(watch)
        explanation = normalize_rule(draft, self.ctx.merchant_names)
        req = self._consent_request(watch)
        proposal = {
            "watch_id": watch["id"],
            "watch_version": watch["version"],
            "profile": watch["authorization_profile"],
            "profile_version": self.ctx.profile(watch["authorization_profile"]).version,
            "rule": explanation.model_dump(),
            "delegation": {
                "merchants": req.merchants,
                "acceptable_items": req.acceptable_items,
                "quantity": req.quantity,
                "currency": req.currency,
                "inclusive_max_minor": req.inclusive_max_minor,
                "not_after": iso(req.not_after),
                "payment_instrument": req.payment_instrument,
                "agent_key_id": self.ctx.trust.by_role["agent"]["kid"],
            },
            "can_activate": not explanation.unknowns,
        }
        now = self.ctx.now()
        with self.ctx.engine.begin() as conn:
            current = repo.get_watch(conn, watch["id"])
            if current["status"] == WatchStatus.DRAFT.value:
                repo.update_watch_versioned(conn, watch["id"], current["version"], {"status": WatchStatus.AWAITING_AUTHORIZATION.value}, now=now)
                repo.add_event(conn, watch["id"], now, "authorization.proposed", watch["user_id"], {"sentences": explanation.sentences, "unknowns": explanation.unknowns}, current["version"] + 1)
                proposal["watch_version"] = current["version"] + 1
        return proposal

    # ---------------------------------------------------------- activate
    def activate(self, watch: Dict[str, Any], expected_version: int) -> Dict[str, Any]:
        """User approved the proposal on the Trusted Surface → sign consent, start watching."""
        now = self.ctx.now()
        if watch["version"] != expected_version:
            raise ServiceError(409, "version_conflict", "watch is at version %d" % watch["version"])
        if watch["status"] not in (WatchStatus.AWAITING_AUTHORIZATION.value, WatchStatus.DRAFT.value):
            raise ServiceError(409, "invalid_state", "watch is %s" % watch["status"])
        draft = self._draft_from_watch(watch)
        unknowns = draft.missing_for_activation()
        if unknowns:
            raise ServiceError(422, "incomplete", "; ".join(unknowns))
        if watch["expires_at"] <= now:
            raise ServiceError(422, "deadline_in_past", "the deadline has already passed")
        req = self._consent_request(watch)
        profile = self.ctx.profile(watch["authorization_profile"])
        consent = profile.issue_consent(req)
        with self.ctx.engine.begin() as conn:
            for a in repo.list_authorizations(conn, watch["id"]):
                if a["status"] == "active":
                    repo.update_authorization(conn, a["id"], status="revoked")
            art = self.ctx.vault.store(conn, "%s.consent" % consent.profile, consent.native, now, watch_id=watch["id"])
            auth_id = new_id("auth")
            repo.insert_authorization(
                conn,
                {
                    "id": auth_id, "watch_id": watch["id"], "watch_version": watch["version"] + 1, "profile": consent.profile, "profile_version": consent.profile_version,
                    "artifact_reference": art["artifact_id"], "agent_key_id": consent.agent_key_id, "consent_reference": consent.consent_reference, "status": "active",
                    "presentation_state": "idle", "expires_at": consent.expires_at, "summary": consent.summary, "created_at": now,
                },
            )
            status = WatchStatus.WATCHING.value if watch["status"] == WatchStatus.AWAITING_AUTHORIZATION.value else transition(transition(watch["status"], WatchStatus.AWAITING_AUTHORIZATION.value), WatchStatus.WATCHING.value)
            ok = repo.update_watch_versioned(conn, watch["id"], watch["version"], {"status": status, "next_check_at": now}, now=now)
            if not ok:
                raise ServiceError(409, "version_conflict", "concurrent modification")
            repo.add_event(conn, watch["id"], now, "authorization.granted", watch["user_id"], {"authorization_id": auth_id, "profile": consent.profile, "profile_version": consent.profile_version, "reference": consent.reference, "artifact_id": art["artifact_id"], "summary": consent.summary}, watch["version"] + 1)
            repo.add_event(conn, watch["id"], now, "watch.activated", watch["user_id"], {"next_check_at": iso(now)}, watch["version"] + 1)
            return repo.get_watch(conn, watch["id"])

    # ------------------------------------------------- pause/resume/cancel
    def pause(self, watch: Dict[str, Any], expected_version: int) -> Dict[str, Any]:
        return self._simple_transition(watch, expected_version, WatchStatus.PAUSED.value, "watch.paused", {"next_check_at": None})

    def resume(self, watch: Dict[str, Any], expected_version: int) -> Dict[str, Any]:
        now = self.ctx.now()
        with self.ctx.engine.begin() as conn:
            auth = repo.active_authorization(conn, watch["id"])
        if auth is None or auth["expires_at"] <= now or auth["watch_version"] > watch["version"]:
            raise ServiceError(409, "authorization_required", "no active authorization; request a new proposal and activate again")
        return self._simple_transition(watch, expected_version, WatchStatus.WATCHING.value, "watch.resumed", {"next_check_at": now})

    def cancel(self, watch: Dict[str, Any], expected_version: int) -> Dict[str, Any]:
        now = self.ctx.now()
        if watch["version"] != expected_version:
            raise ServiceError(409, "version_conflict", "watch is at version %d" % watch["version"])
        with self.ctx.engine.begin() as conn:
            status = WatchStatus(watch["status"])
            if status in ACTIVE_STATUSES or status == WatchStatus.DRAFT:
                values = {"status": WatchStatus.CANCELLED.value, "cancel_requested": True, "cancelled_at": now, "next_check_at": None}
                note = "cancelled before any submission"
                for a in repo.list_authorizations(conn, watch["id"]):
                    if a["status"] == "active":
                        repo.update_authorization(conn, a["id"], status="revoked")
            elif status in IN_FLIGHT_STATUSES:
                values = {"cancel_requested": True}
                note = "cancel requested after the purchase claim; future activity stops, but an order already submitted to the merchant may not be cancellable"
            else:
                raise ServiceError(409, "invalid_state", "watch is already %s" % status.value)
            if not repo.update_watch_versioned(conn, watch["id"], watch["version"], values, now=now):
                raise ServiceError(409, "version_conflict", "concurrent modification")
            repo.add_event(conn, watch["id"], now, "watch.cancel_requested" if status in IN_FLIGHT_STATUSES else "watch.cancelled", watch["user_id"], {"note": note, "previous_status": status.value}, watch["version"] + 1)
            return repo.get_watch(conn, watch["id"])

    def _simple_transition(self, watch, expected_version, new_status, event, extra):
        now = self.ctx.now()
        if watch["version"] != expected_version:
            raise ServiceError(409, "version_conflict", "watch is at version %d" % watch["version"])
        try:
            status = transition(watch["status"], new_status)
        except IllegalTransition as e:
            raise ServiceError(409, "invalid_state", str(e))
        with self.ctx.engine.begin() as conn:
            values = dict(extra, status=status)
            if not repo.update_watch_versioned(conn, watch["id"], watch["version"], values, now=now):
                raise ServiceError(409, "version_conflict", "concurrent modification")
            repo.add_event(conn, watch["id"], now, event, watch["user_id"], {}, watch["version"] + 1)
            return repo.get_watch(conn, watch["id"])

    # ---------------------------------------------------------- read side
    def timeline(self, watch_id: str) -> List[Dict[str, Any]]:
        with self.ctx.engine.begin() as conn:
            return repo.list_events(conn, watch_id)

    def observations(self, watch_id: str) -> List[Dict[str, Any]]:
        with self.ctx.engine.begin() as conn:
            return repo.list_observations(conn, watch_id)

    def purchase(self, watch_id: str) -> Dict[str, Any]:
        with self.ctx.engine.begin() as conn:
            attempts = repo.attempts_for_watch(conn, watch_id)
            cases = repo.list_cases(conn, watch_id)
            auths = repo.list_authorizations(conn, watch_id)
            arts = repo.list_artifacts(conn, watch_id=watch_id)
            outbound = {a["id"]: repo.outbound_for_attempt(conn, a["id"]) for a in attempts}
            notes = repo.list_notifications(conn, watch_id)
        return {
            "attempts": attempts,
            "outbound_events": outbound,
            "reconciliation_cases": cases,
            "authorizations": [{k: v for k, v in a.items()} for a in auths],
            "evidence": [{"artifact_id": a["id"], "kind": a["kind"], "digest": a["digest"], "attempt_id": a["attempt_id"], "created_at": a["created_at"]} for a in arts],
            "notifications": notes,
        }

    # ------------------------------------------------------------ helpers
    def _draft_from_watch(self, w: Dict[str, Any]) -> WatchDraft:
        from watch_domain.models import PriceRule, ProductConstraints

        return WatchDraft(
            original_request=w.get("original_request"),
            product=ProductConstraints(**w["product_constraints"]),
            merchant_skus=w["merchant_sku_mapping"],
            quantity=w["quantity"], currency=w["currency"],
            price_rule=PriceRule(operator=w["price_operator"], delivered_total_minor=w["threshold_minor"]),
            allowed_merchants=w["allowed_merchants"], destination_id=w["destination_id"], expires_at=w["expires_at"], timezone=w["timezone"],
            max_purchases=w["max_purchases"], authorization_profile=w["authorization_profile"], poll_interval_seconds=w["poll_interval_seconds"],
        )

    def _consent_request(self, w: Dict[str, Any]) -> ConsentRequest:
        merchants = []
        items = []
        for m in w["allowed_merchants"]:
            cat = self.catalogs[m]
            merchants.append({"id": m, "name": cat["name"], "website": cat["website"]})
            sku = w["merchant_sku_mapping"][m]
            item = catalog_item(cat, sku)
            items.append({"id": sku, "title": item["title"] if item else sku, "merchant_id": m})
        ceiling = inclusive_ceiling(w["price_operator"], w["threshold_minor"])
        return ConsentRequest(
            watch_id=w["id"], watch_version=w["version"] + 1, user_id=w["user_id"], agent_id=w["agent_id"],
            merchants=merchants, acceptable_items=items, quantity=w["quantity"], currency=w["currency"],
            inclusive_max_minor=int(ceiling), not_after=w["expires_at"], issued_at=self.ctx.now(),
            payment_instrument={"type": "card", "id": "pi_fixture_%s" % w["user_id"], "description": "Fixture card ••••4242"},
            prompt_summary=w.get("original_request") or "Buy %s once when the delivered total is %s %d cents" % (w["product_constraints"]["title"], w["price_operator"], w["threshold_minor"]),
        )
