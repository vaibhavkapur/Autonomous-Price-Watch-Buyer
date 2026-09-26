from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

import httpx
from joserfc.jwk import ECKey

from authorization_profiles import sdjwt
from common.util import canonical_json

UCP_VERSION = "2026-08-25"
AGENT_PROFILE_URL = "https://agent.pricewatch.local/profiles/price-watch-agent.json"
CHECKOUT_CAPABILITY = "dev.ucp.shopping.checkout"
AP2_HANDLER_FAMILY = "dev.ucp.ap2_mandate_compatible_handlers"
VI_HANDLER_FAMILY = "local.pricewatch.vi_mandate_handlers"


class MerchantError(Exception):
    def __init__(self, merchant_id: str, status: int, code: str, content: str = ""):
        super().__init__("%s: HTTP %d %s %s" % (merchant_id, status, code, content))
        self.merchant_id = merchant_id
        self.status = status
        self.code = code
        self.content = content


class MerchantTimeout(Exception):
    """The request may or may not have been processed by the merchant."""


@dataclass
class MerchantEndpoint:
    merchant_id: str
    base_url: str
    client: httpx.Client  # a real httpx.Client or starlette TestClient (also an httpx.Client)


FaultHook = Callable[[str, str], bool]


class UCPClient:
    def __init__(self, endpoints: Dict[str, MerchantEndpoint], *, fault_hook: Optional[FaultHook] = None, timeout: float = 10.0):
        self.endpoints = endpoints
        self.fault_hook = fault_hook or (lambda target, fault: False)
        self.timeout = timeout
        self._profiles: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    # ----------------------------------------------------------- plumbing
    def _ep(self, merchant_id: str) -> MerchantEndpoint:
        try:
            return self.endpoints[merchant_id]
        except KeyError:
            raise MerchantError(merchant_id, 0, "unknown_merchant", "merchant is not configured for this platform")

    def _headers(self, idempotency_key: Optional[str] = None) -> Dict[str, str]:
        h = {"UCP-Agent": 'profile="%s"' % AGENT_PROFILE_URL, "Accept": "application/json"}
        if idempotency_key:
            h["Idempotency-Key"] = idempotency_key
        return h

    def _request(self, merchant_id: str, method: str, path: str, *, json: Any = None, params: Optional[Dict[str, Any]] = None, idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        ep = self._ep(merchant_id)
        url = ep.base_url.rstrip("/") + path
        try:
            resp = ep.client.request(method, url, json=json, params=params, headers=self._headers(idempotency_key), timeout=self.timeout)
        except (httpx.TimeoutException, httpx.NetworkError) as e:
            raise MerchantTimeout("%s %s: %s" % (method, url, e.__class__.__name__))
        try:
            body = resp.json()
        except ValueError:
            raise MerchantError(merchant_id, resp.status_code, "invalid_json", resp.text[:200])
        if resp.status_code >= 400 or (isinstance(body, dict) and body.get("ucp", {}).get("status") == "error"):
            raise MerchantError(merchant_id, resp.status_code, str(body.get("code", "error")), str(body.get("content", "")))
        return body

    # ---------------------------------------------------------- discovery
    def discover(self, merchant_id: str, force: bool = False) -> Dict[str, Any]:
        with self._lock:
            if not force and merchant_id in self._profiles:
                return self._profiles[merchant_id]
        profile = self._request(merchant_id, "GET", "/.well-known/ucp")
        ucp = profile.get("ucp") or {}
        if ucp.get("version") != UCP_VERSION:
            raise MerchantError(merchant_id, 200, "version_unsupported", "merchant advertises UCP %s, platform pinned to %s" % (ucp.get("version"), UCP_VERSION))
        caps = ucp.get("capabilities") or {}
        if not any(c.get("version") == UCP_VERSION for c in caps.get(CHECKOUT_CAPABILITY, [])):
            raise MerchantError(merchant_id, 200, "capability_missing", "no %s at %s" % (CHECKOUT_CAPABILITY, UCP_VERSION))
        if not profile.get("keys"):
            raise MerchantError(merchant_id, 200, "keys_missing", "business profile publishes no signing keys")
        with self._lock:
            self._profiles[merchant_id] = profile
        return profile

    def handler_id(self, merchant_id: str, profile_name: str) -> str:
        profile = self.discover(merchant_id)
        family = AP2_HANDLER_FAMILY if profile_name == "ap2" else VI_HANDLER_FAMILY
        handlers = (profile["ucp"].get("payment_handlers") or {}).get(family) or []
        if not handlers:
            raise MerchantError(merchant_id, 200, "handler_missing", "merchant advertises no %s" % family)
        return handlers[0]["id"]

    def merchant_descriptor(self, merchant_id: str) -> Dict[str, str]:
        return dict(self.discover(merchant_id)["merchant"])

    # ------------------------------------------------------- offer feed
    def feed_quote(self, merchant_id: str, sku: str, quantity: int, address: Dict[str, Any]) -> Dict[str, Any]:
        return self._request(merchant_id, "GET", "/app-feed/offers", params={"sku": sku, "quantity": quantity, "country": address.get("country", "US"), "postal_code": address.get("postal_code", "")})

    # --------------------------------------------------------- checkout
    def create_checkout(self, merchant_id: str, sku: str, quantity: int, buyer: Dict[str, Any], address: Dict[str, Any], idempotency_key: str) -> Dict[str, Any]:
        body = {"line_items": [{"item": {"id": sku}, "quantity": quantity}], "buyer": buyer, "fulfillment": {"address": address}}
        return self._request(merchant_id, "POST", "/checkout-sessions", json=body, idempotency_key=idempotency_key)

    def get_checkout(self, merchant_id: str, checkout_id: str) -> Dict[str, Any]:
        return self._request(merchant_id, "GET", "/checkout-sessions/%s" % checkout_id)

    def cancel_checkout(self, merchant_id: str, checkout_id: str) -> Optional[Dict[str, Any]]:
        try:
            return self._request(merchant_id, "POST", "/checkout-sessions/%s/cancel" % checkout_id)
        except (MerchantError, MerchantTimeout):
            return None

    def complete_checkout(self, merchant_id: str, checkout_id: str, payload: Dict[str, Any], idempotency_key: str) -> Dict[str, Any]:
        """Complete Checkout. If the response is lost we raise MerchantTimeout and
        the caller MUST treat the outcome as unknown (recover via Get Checkout)."""
        body = self._request(merchant_id, "POST", "/checkout-sessions/%s/complete" % checkout_id, json=payload, idempotency_key=idempotency_key)
        if self.fault_hook("adapter", "drop_complete_response"):
            # The merchant processed the request; the response is discarded at the network boundary.
            raise MerchantTimeout("response to Complete Checkout dropped (fault injection)")
        return body

    def get_order(self, merchant_id: str, order_id: str) -> Dict[str, Any]:
        return self._request(merchant_id, "GET", "/orders/%s" % order_id)

    # ------------------------------------------------- checkout_jwt proof
    def verify_checkout_jwt(self, merchant_id: str, checkout: Dict[str, Any]) -> Dict[str, Any]:
        """Verify the merchant-signed Checkout JWT against the published keys and
        confirm it describes the checkout body we received. Returns the JWT payload."""
        token = ((checkout.get("ap2") or {}).get("checkout_jwt"))
        if not token:
            raise MerchantError(merchant_id, 200, "checkout_jwt_missing", "merchant did not return a signed checkout")
        profile = self.discover(merchant_id)
        header, _ = sdjwt.decode_unverified(token)
        jwk = next((k for k in profile["keys"] if k.get("kid") == header.get("kid")), None)
        if jwk is None:
            raise MerchantError(merchant_id, 200, "checkout_jwt_untrusted_key", "kid %r not in merchant profile keys" % header.get("kid"))
        if header.get("alg") != "ES256":
            raise MerchantError(merchant_id, 200, "checkout_jwt_alg", "checkout_jwt must use ECDSA (ES256), not a deterministic signature")
        try:
            _, payload = sdjwt.verify_jwt(token, ECKey.import_key(jwk))
        except sdjwt.SDJWTError as e:
            raise MerchantError(merchant_id, 200, "checkout_jwt_invalid", str(e))
        for f in ("id", "merchant", "line_items", "totals", "currency", "status"):
            if canonical_json(payload.get(f)) != canonical_json(checkout.get(f)):
                raise MerchantError(merchant_id, 200, "checkout_jwt_mismatch", "field %s differs between signed checkout and response" % f)
        if payload["merchant"].get("id") != merchant_id:
            raise MerchantError(merchant_id, 200, "checkout_jwt_merchant", "signed checkout names merchant %r" % payload["merchant"].get("id"))
        return payload
