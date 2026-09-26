from __future__ import annotations

import copy
import threading
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
from joserfc.jwk import ECKey

from authorization_profiles import sdjwt
from authorization_profiles.ap2 import make_receipt
from authorization_profiles.keys import TrustStore
from authorization_profiles.payment_sim import MerchantPaymentProcessorSim
from authorization_profiles.registry import get_profile
from authorization_profiles.keys import KeyRing
from common.clock import Clock, SystemClock, iso
from common.util import canonical_json, new_id, sha256_b64url

UCP_VERSION = "2026-08-25"
AP2_HANDLER_ID = "ap2_fixture_handler"
VI_HANDLER_ID = "vi_fixture_handler"
CHECKOUT_TTL = timedelta(hours=6)
QUOTE_TTL = timedelta(minutes=10)


@dataclass
class MerchantConfig:
    merchant_id: str
    name: str
    website: str
    catalog: Dict[str, Any]
    signing_key: ECKey
    trust: TrustStore
    base_url: str = "http://merchant.local"
    clock: Clock = field(default_factory=SystemClock)

    @property
    def descriptor(self) -> Dict[str, str]:
        return {"id": self.merchant_id, "name": self.name, "website": self.website}


class MerchantState:
    """In-memory business state. Thread-safe via a single lock."""

    def __init__(self, cfg: MerchantConfig):
        self.cfg = cfg
        self.lock = threading.RLock()
        self.items: Dict[str, Dict[str, Any]] = {}
        for it in cfg.catalog["items"]:
            self.items[it["sku"]] = dict(it, tax_minor=None, tax_unknown=False)
        self.checkouts: Dict[str, Dict[str, Any]] = {}
        self.orders: Dict[str, Dict[str, Any]] = {}
        self.create_idem: Dict[str, str] = {}
        self.complete_idem: Dict[str, Dict[str, Any]] = {}
        self.faults: Dict[str, int] = {}
        self.mpp = MerchantPaymentProcessorSim(cfg.trust)
        self.verification_log: List[Dict[str, Any]] = []

    # ---- faults
    def consume_fault(self, name: str) -> bool:
        with self.lock:
            if self.faults.get(name, 0) > 0:
                self.faults[name] -= 1
                return True
            return False

    # ---- pricing
    def quote(self, sku: str, quantity: int) -> Optional[Dict[str, Any]]:
        it = self.items.get(sku)
        if not it:
            return None
        subtotal = int(it["price_minor"]) * quantity
        tax = None
        if not it.get("tax_unknown"):
            tax = int(it["tax_minor"]) if it.get("tax_minor") is not None else (subtotal * int(self.cfg.catalog.get("tax_rate_bps", 0)) + 5000) // 10000
        return {
            "subtotal": subtotal,
            "shipping": int(it.get("shipping_minor", 0)),
            "tax": tax,
            "fees": int(it.get("fees_minor", 0)),
            "stock": int(it.get("stock", 0)),
        }

    def sign_checkout(self, checkout: Dict[str, Any]) -> str:
        snapshot = {k: checkout[k] for k in ("id", "merchant", "line_items", "totals", "currency", "status", "expires_at") if k in checkout}
        snapshot["iat"] = self.cfg.clock.epoch()
        return sdjwt.sign_jwt({"alg": "ES256", "typ": "JWT", "kid": self.cfg.signing_key.as_dict(private=False)["kid"]}, snapshot, self.cfg.signing_key)

    def build_checkout(self, checkout_id: str, line_items_req: List[Dict[str, Any]], buyer: Optional[Dict[str, Any]], fulfillment: Optional[Dict[str, Any]], now) -> Dict[str, Any]:
        messages: List[Dict[str, Any]] = []
        line_items: List[Dict[str, Any]] = []
        totals = {"subtotal": 0, "fulfillment": 0, "tax": 0, "fee": 0}
        tax_known = True
        for idx, li in enumerate(line_items_req):
            sku = (li.get("item") or {}).get("id")
            qty = int(li.get("quantity", 1))
            q = self.quote(sku, qty)
            it = self.items.get(sku)
            if q is None or it is None:
                messages.append({"type": "error", "code": "item_not_found", "severity": "recoverable", "path": "$.line_items[%d].item.id" % idx, "content": "Unknown item %s" % sku})
                continue
            if q["stock"] < qty:
                messages.append({"type": "error", "code": "out_of_stock", "severity": "recoverable", "path": "$.line_items[%d]" % idx, "content": "Only %d unit(s) available" % q["stock"]})
            li_totals = [{"type": "subtotal", "amount": q["subtotal"]}, {"type": "total", "amount": q["subtotal"]}]
            line_items.append({"id": "li_%d" % (idx + 1), "item": {"id": sku, "title": it["title"], "price": int(it["price_minor"])}, "quantity": qty, "totals": li_totals})
            totals["subtotal"] += q["subtotal"]
            totals["fulfillment"] += q["shipping"]
            totals["fee"] += q["fees"]
            if q["tax"] is None:
                tax_known = False
            else:
                totals["tax"] += q["tax"]
        if not fulfillment or not (fulfillment.get("address") or {}).get("postal_code"):
            messages.append({"type": "error", "code": "missing", "severity": "recoverable", "path": "$.fulfillment.address", "content": "A delivery address is required to compute shipping and tax"})
        if not tax_known:
            messages.append({"type": "error", "code": "tax_pending", "severity": "requires_buyer_review", "path": "$.totals", "content": "Tax could not be determined for this destination"})
        totals_list = [{"type": "subtotal", "amount": totals["subtotal"]}, {"type": "fulfillment", "amount": totals["fulfillment"]}]
        if tax_known:
            totals_list.append({"type": "tax", "amount": totals["tax"]})
        if totals["fee"]:
            totals_list.append({"type": "fee", "amount": totals["fee"]})
        if tax_known:
            totals_list.append({"type": "total", "amount": totals["subtotal"] + totals["fulfillment"] + totals["tax"] + totals["fee"]})
        errors = [m for m in messages if m["type"] == "error"]
        if any(m.get("severity", "").startswith("requires_") for m in errors):
            status = "requires_escalation"
        elif errors:
            status = "incomplete"
        else:
            status = "ready_for_complete"
        checkout = {
            "ucp": {"version": UCP_VERSION, "status": "success", "payment_handlers": self.payment_handlers()},
            "id": checkout_id,
            "status": status,
            "currency": self.cfg.catalog.get("currency", "USD"),
            "merchant": self.cfg.descriptor,
            "line_items": line_items,
            "buyer": buyer or {},
            "fulfillment": fulfillment or {},
            "totals": totals_list,
            "messages": messages,
            "links": [
                {"type": "privacy_policy", "url": self.cfg.website + "/privacy"},
                {"type": "terms_of_service", "url": self.cfg.website + "/tos"},
            ],
            "expires_at": iso(now + CHECKOUT_TTL),
            "continue_url": "%s/checkout/%s" % (self.cfg.website, checkout_id),
            "payment": {"handlers": self.payment_handlers()},
        }
        checkout["ap2"] = {"checkout_jwt": self.sign_checkout(checkout)}
        return checkout

    def payment_handlers(self) -> Dict[str, Any]:
        return {
            "dev.ucp.ap2_mandate_compatible_handlers": [
                {"id": AP2_HANDLER_ID, "version": UCP_VERSION, "spec": "https://ap2-protocol.org/ap2/specification/", "schema": "https://ap2-protocol.org/schemas/ap2-handler.json", "available_instruments": [{"type": "ap2_mandate"}], "ap2_version": "0.2"}
            ],
            # Application-defined handler family for the Verifiable Intent profile (not a UCP-registered namespace).
            "local.pricewatch.vi_mandate_handlers": [
                {"id": VI_HANDLER_ID, "version": UCP_VERSION, "spec": "https://github.com/agent-intent/verifiable-intent", "available_instruments": [{"type": "vi_mandate"}], "vi_version": "0.1-draft"}
            ],
        }

    def profile_document(self) -> Dict[str, Any]:
        base = self.cfg.base_url
        return {
            "ucp": {
                "version": UCP_VERSION,
                "services": {
                    "dev.ucp.shopping": [
                        {"version": UCP_VERSION, "spec": "https://ucp.dev/%s/specification/overview" % UCP_VERSION, "schema": "https://ucp.dev/%s/services/shopping.openapi.json" % UCP_VERSION, "transport": "rest", "endpoint": base}
                    ]
                },
                "capabilities": {
                    "dev.ucp.shopping.checkout": [{"version": UCP_VERSION, "spec": "https://ucp.dev/%s/specification/shopping/checkout" % UCP_VERSION, "schema": "https://ucp.dev/%s/schemas/shopping/checkout.json" % UCP_VERSION}],
                    "dev.ucp.common.payment.ap2_mandate": [{"version": UCP_VERSION, "spec": "https://ucp.dev/%s/specification/payment/ap2-mandate" % UCP_VERSION, "extends": "dev.ucp.shopping.checkout"}],
                },
                "payment_handlers": self.payment_handlers(),
            },
            "keys": [self.cfg.signing_key.as_dict(private=False)],
            "merchant": self.cfg.descriptor,
            "application_feeds": {"offers": {"endpoint": base + "/app-feed/offers", "note": "Application feed (not a UCP capability). Delivered-price quotes for polling; the UCP checkout is authoritative."}},
        }


def _error(status: int, code: str, content: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"ucp": {"version": UCP_VERSION, "status": "error"}, "code": code, "content": content})


def create_merchant_app(cfg: MerchantConfig, state: Optional[MerchantState] = None) -> FastAPI:
    st = state or MerchantState(cfg)
    app = FastAPI(title="UCP merchant fixture: %s" % cfg.name)
    app.state.merchant = st

    @app.get("/.well-known/ucp")
    def well_known():
        return st.profile_document()

    @app.get("/.well-known/jwks.json")
    def jwks():
        return {"keys": [cfg.signing_key.as_dict(private=False)]}

    # ------------------------------------------------------- application feed
    @app.get("/app-feed/offers")
    def offers(sku: str, quantity: int = 1, country: str = "US", postal_code: str = ""):
        now = cfg.clock.now()
        with st.lock:
            it = st.items.get(sku)
            q = st.quote(sku, quantity)
        if not it or q is None:
            return _error(404, "item_not_found", "unknown sku %s" % sku)
        body = {
            "source": "application_feed",
            "merchant_id": cfg.merchant_id,
            "offer_id": "%s:%s:%d" % (cfg.merchant_id, sku, cfg.clock.epoch()),
            "sku": sku,
            "title": it["title"],
            "attributes": it.get("attributes", {}),
            "quantity": quantity,
            "quantity_available": q["stock"],
            "currency": cfg.catalog.get("currency", "USD"),
            "item_price_minor": int(it["price_minor"]),
            "shipping_minor": q["shipping"] if postal_code else None,
            "fees_minor": q["fees"],
            "quoted_at": iso(now),
            "valid_until": iso(now + QUOTE_TTL),
            "delivery_estimate": it.get("delivery_estimate"),
        }
        if q["tax"] is not None and postal_code:
            body["tax_minor"] = q["tax"]
        return body

    # ------------------------------------------------------- UCP checkout
    @app.post("/checkout-sessions")
    async def create_checkout(request: Request, idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key")):
        payload = await request.json()
        now = cfg.clock.now()
        with st.lock:
            if idempotency_key and idempotency_key in st.create_idem:
                return st.checkouts[st.create_idem[idempotency_key]]
            if st.consume_fault("reject_checkout_once"):
                return _error(503, "service_unavailable", "fault: reject_checkout_once")
            checkout_id = new_id("chk")
            checkout = st.build_checkout(checkout_id, payload.get("line_items", []), payload.get("buyer"), payload.get("fulfillment"), now)
            st.checkouts[checkout_id] = checkout
            if idempotency_key:
                st.create_idem[idempotency_key] = checkout_id
            return checkout

    @app.get("/checkout-sessions/{checkout_id}")
    def get_checkout(checkout_id: str):
        with st.lock:
            c = st.checkouts.get(checkout_id)
            if not c:
                return _error(404, "not_found", "unknown checkout")
            if c["status"] in ("completed", "canceled"):
                return c
            now = cfg.clock.now()
            from common.clock import parse_iso

            if parse_iso(c["expires_at"]) <= now:
                c["status"] = "canceled"
                c["messages"] = [{"type": "error", "code": "expired", "severity": "recoverable", "content": "checkout session expired"}]
                c["ap2"] = {"checkout_jwt": st.sign_checkout(c)}
            return c

    @app.put("/checkout-sessions/{checkout_id}")
    async def update_checkout(checkout_id: str, request: Request):
        payload = await request.json()
        with st.lock:
            c = st.checkouts.get(checkout_id)
            if not c:
                return _error(404, "not_found", "unknown checkout")
            if c["status"] in ("completed", "canceled", "complete_in_progress"):
                return _error(409, "invalid_state", "checkout is %s" % c["status"])
            new = st.build_checkout(checkout_id, payload.get("line_items", c["line_items"]), payload.get("buyer", c.get("buyer")), payload.get("fulfillment", c.get("fulfillment")), cfg.clock.now())
            st.checkouts[checkout_id] = new
            return new

    @app.post("/checkout-sessions/{checkout_id}/cancel")
    def cancel_checkout(checkout_id: str):
        with st.lock:
            c = st.checkouts.get(checkout_id)
            if not c:
                return _error(404, "not_found", "unknown checkout")
            if c["status"] in ("completed", "canceled"):
                return _error(409, "invalid_state", "checkout cannot be canceled from %s" % c["status"])
            c["status"] = "canceled"
            c.pop("continue_url", None)
            c["ap2"] = {"checkout_jwt": st.sign_checkout(c)}
            return c

    @app.post("/checkout-sessions/{checkout_id}/complete")
    async def complete_checkout(checkout_id: str, request: Request, idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key")):
        payload = await request.json()
        now = cfg.clock.now()
        if not idempotency_key:
            return _error(400, "idempotency_key_required", "Complete Checkout requires an Idempotency-Key header")
        digest = sha256_b64url(canonical_json(payload).encode("utf-8"))
        with st.lock:
            prior = st.complete_idem.get(idempotency_key)
            if prior:
                if prior["digest"] != digest:
                    return _error(409, "idempotency_conflict", "Idempotency key reused with a different payload")
                return JSONResponse(status_code=prior["status"], content=prior["body"])
            c = st.checkouts.get(checkout_id)
            if not c:
                return _error(404, "not_found", "unknown checkout")
            if st.consume_fault("fail_complete_once"):
                # Fails *before* any state change: safe to retry with the same key.
                return _error(500, "internal_error", "fault: fail_complete_once")
            if c["status"] == "completed":
                return _error(409, "invalid_state", "checkout already completed under a different idempotency key")
            if c["status"] != "ready_for_complete":
                return _error(409, "invalid_state", "checkout is %s, not ready_for_complete" % c["status"])
            from common.clock import parse_iso

            if parse_iso(c["expires_at"]) <= now:
                return _error(409, "expired", "checkout session expired")

            checkout_jwt = c["ap2"]["checkout_jwt"]
            total = next(t["amount"] for t in c["totals"] if t["type"] == "total")
            instruments = (payload.get("payment") or {}).get("instruments") or []
            if len(instruments) != 1:
                return _error(400, "invalid", "exactly one payment instrument is required")
            inst = instruments[0]
            handler = inst.get("handler_id")
            if handler not in (AP2_HANDLER_ID, VI_HANDLER_ID):
                return _error(400, "invalid_handler", "handler_id %r is not advertised" % handler)

            # --- authorization evidence (profile chosen by the presented artifact family)
            if "ap2" in payload and handler == AP2_HANDLER_ID:
                profile_name, merchant_payload = "ap2", payload["ap2"]
            elif "vi" in payload and handler == VI_HANDLER_ID:
                profile_name, merchant_payload = "vi", payload
            else:
                return _error(400, "authorization_required", "Complete Checkout requires an AP2 checkout mandate or a VI L3b credential matching the handler")
            profile = get_profile(profile_name, KeyRing({}), cfg.trust)
            result = profile.verify_for_merchant(merchant_payload, checkout_jwt, cfg.descriptor, now)
            st.verification_log.append({"checkout_id": checkout_id, "profile": profile_name, "ok": result.ok, "code": result.code, "description": result.description, "at": iso(now)})
            if not result.ok:
                body = copy.deepcopy(c)
                body["messages"] = [{"type": "error", "code": result.code or "invalid_mandate", "severity": "requires_buyer_review", "content": result.description or ""}]
                if profile_name == "ap2":
                    body["ap2"]["checkout_receipt"] = make_receipt(cfg.signing_key, cfg.website, status="Error", reference=_reference_for_error(merchant_payload), now=now, error=result.code, error_description=result.description)
                else:
                    body["vi"] = {"verification": {"ok": False, "error": result.code, "error_description": result.description}}
                st.complete_idem[idempotency_key] = {"digest": digest, "status": 200, "body": body}
                return body

            # --- payment credential (MPP role)
            token = (inst.get("credential") or {}).get("token")
            if not token:
                return _error(400, "invalid", "payment credential token missing")
            checkout_hash = sha256_b64url(checkout_jwt.encode("ascii"))
            cap = st.mpp.capture(token, checkout_hash=checkout_hash, amount_minor=total, currency=c["currency"], merchant_id=cfg.merchant_id, now=now)
            if not cap["ok"]:
                body = copy.deepcopy(c)
                body["messages"] = [{"type": "error", "code": "payment_declined", "severity": "requires_buyer_review", "content": cap.get("error", "")}]
                st.complete_idem[idempotency_key] = {"digest": digest, "status": 200, "body": body}
                return body

            # --- place the order (exactly once per checkout)
            for li in c["line_items"]:
                it = st.items[li["item"]["id"]]
                it["stock"] = int(it.get("stock", 0)) - int(li["quantity"])
            order_id = new_id("ord")
            order = {
                "id": order_id,
                "checkout_id": checkout_id,
                "merchant_id": cfg.merchant_id,
                "permalink_url": "%s/orders/%s" % (cfg.website, order_id),
                "line_items": c["line_items"],
                "totals": c["totals"],
                "currency": c["currency"],
                "payment": {"payment_id": cap["payment_id"], "psp_confirmation_id": cap["psp_confirmation_id"], "state": "captured"},
                "authorization": {"profile": profile_name, "reference": result.details.get("reference"), "checkout_hash": checkout_hash},
                "placed_at": iso(now),
                "idempotency_key": idempotency_key,
            }
            st.orders[order_id] = order
            c["status"] = "completed"
            c["order"] = {"id": order_id, "permalink_url": order["permalink_url"]}
            c.pop("continue_url", None)
            c["ap2"] = {"checkout_jwt": checkout_jwt}
            if profile_name == "ap2":
                c["ap2"]["checkout_receipt"] = make_receipt(cfg.signing_key, cfg.website, status="Success", reference=result.details["reference"], now=now, order_id=order_id)
            else:
                c["vi"] = {"verification": {"ok": True, "reference": result.details["reference"], "constraints": result.details.get("constraints")},
                           "merchant_attestation": sdjwt.sign_jwt({"alg": "ES256", "typ": "JWT", "kid": cfg.signing_key.as_dict(private=False)["kid"]}, {"iss": cfg.website, "iat": int(now.timestamp()), "vct": "local.pricewatch.vi.merchant_attestation", "reference": result.details["reference"], "order_id": order_id}, cfg.signing_key)}
            st.complete_idem[idempotency_key] = {"digest": digest, "status": 200, "body": copy.deepcopy(c)}
            return c

    @app.get("/orders/{order_id}")
    def get_order(order_id: str):
        with st.lock:
            o = st.orders.get(order_id)
        if not o:
            return _error(404, "not_found", "unknown order")
        return o

    # ------------------------------------------------------- demo controls
    @app.post("/demo/price-scenario")
    async def price_scenario(request: Request):
        payload = await request.json()
        with st.lock:
            it = st.items.get(payload["sku"])
            if not it:
                return _error(404, "item_not_found", "unknown sku")
            for k_src, k_dst in (("item_price_minor", "price_minor"), ("shipping_minor", "shipping_minor"), ("fees_minor", "fees_minor"), ("stock", "stock"), ("tax_minor", "tax_minor")):
                if k_src in payload:
                    it[k_dst] = payload[k_src]
            if "tax_unknown" in payload:
                it["tax_unknown"] = bool(payload["tax_unknown"])
            return {"ok": True, "item": it}

    @app.post("/demo/faults")
    async def faults(request: Request):
        payload = await request.json()
        with st.lock:
            st.faults[payload["fault"]] = int(payload.get("count", 1))
            return {"ok": True, "faults": st.faults}

    @app.get("/demo/orders")
    def list_orders():
        with st.lock:
            return {"orders": list(st.orders.values())}

    @app.get("/demo/state")
    def demo_state():
        with st.lock:
            return {"items": st.items, "checkouts": len(st.checkouts), "orders": len(st.orders), "faults": st.faults, "verification_log": st.verification_log}

    @app.post("/demo/reset")
    def reset():
        with st.lock:
            fresh = MerchantState(cfg)
            st.items, st.checkouts, st.orders, st.create_idem, st.complete_idem, st.faults = fresh.items, fresh.checkouts, fresh.orders, fresh.create_idem, fresh.complete_idem, fresh.faults
            st.mpp = fresh.mpp
            st.verification_log = []
            return {"ok": True}

    return app


def _reference_for_error(merchant_payload: Dict[str, Any]) -> str:
    try:
        from authorization_profiles.ap2 import _split_chain

        segs = _split_chain(merchant_payload.get("checkout_mandate", ""))
        if len(segs) == 2:
            return sdjwt.sd_hash(sdjwt.serialize(segs[1][0], segs[1][1]))
    except Exception:  # pragma: no cover
        pass
    return "unknown"
