"""Deterministic test harness: two in-process UCP merchant fixtures, a manual
clock, a temp SQLite database, one agent, one user."""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from starlette.testclient import TestClient

from authorization_profiles.keys import KeyRing
from common.clock import ManualClock
from common.config import Settings
from merchant_fixture import MerchantConfig, MerchantState, create_merchant_app
from persistence import make_engine
from product_identity import load_merchant_catalog
from purchase_coordinator import Worker
from purchase_coordinator.bootstrap import build_context
from ucp_adapter import MerchantEndpoint
from watch_domain.models import PriceRule, ProductConstraints, WatchDraft
from watch_domain.service import WatchService

T0 = datetime(2026, 9, 26, 12, 0, 0, tzinfo=timezone.utc)
DEADLINE = datetime(2026, 9, 27, 18, 0, 0, tzinfo=timezone.utc)


class Harness:
    def __init__(self, *, clock: Optional[ManualClock] = None, db_path: Optional[str] = None, merchant_ids=("merchant_a", "merchant_b"), worker_id: str = "worker-1"):
        self.clock = clock or ManualClock(T0)
        self.keys = KeyRing.generate()
        self.trust = self.keys.trust_store()
        if db_path is None:
            fd, db_path = tempfile.mkstemp(suffix=".sqlite3", prefix="pricewatch-test-")
            os.close(fd)
        self.db_path = db_path
        self.settings = Settings(
            profile="development", database_url="sqlite:///%s" % db_path, poll_interval_seconds=60, poll_jitter_seconds=0, lease_seconds=120,
            merchant_min_interval_ms=0, merchant_backoff_base_seconds=300, artifact_encryption_key="test-key",
            merchant_base_urls={m: "http://%s.test" % m for m in merchant_ids},
        )
        self.merchants: Dict[str, MerchantState] = {}
        self.merchant_clients: Dict[str, TestClient] = {}
        endpoints = {}
        for mid in merchant_ids:
            catalog = load_merchant_catalog(mid)
            cfg = MerchantConfig(mid, catalog["name"], catalog["website"], catalog, self.keys.private(mid), self.trust, base_url="http://%s.test" % mid, clock=self.clock)
            state = MerchantState(cfg)
            app = create_merchant_app(cfg, state)
            client = TestClient(app, base_url="http://%s.test" % mid)
            self.merchants[mid] = state
            self.merchant_clients[mid] = client
            endpoints[mid] = MerchantEndpoint(mid, "http://%s.test" % mid, client)
        self.endpoints = endpoints
        self.engine = make_engine(self.settings.database_url)
        self.ctx = build_context(self.settings, clock=self.clock, worker_id=worker_id, endpoints=endpoints, engine=self.engine, keys=self.keys)
        self.ctx.merchant_names = {mid: load_merchant_catalog(mid)["name"] for mid in merchant_ids}
        self.service = WatchService(self.ctx, {mid: load_merchant_catalog(mid) for mid in merchant_ids})
        self.worker = Worker(self.ctx)

    # ---- another worker process sharing the same database
    def new_worker(self, worker_id: str) -> Worker:
        ctx = build_context(self.settings, clock=self.clock, worker_id=worker_id, endpoints=self.endpoints, engine=self.engine, keys=self.keys, seed=False)
        ctx.merchant_names = self.ctx.merchant_names
        return Worker(ctx)

    # ---- watch helpers
    def draft(self, **overrides) -> WatchDraft:
        base: Dict[str, Any] = dict(
            original_request="Buy this exact keyboard if its delivered price drops below $100 before Sunday. Use only these two merchants and buy it once.",
            product=ProductConstraints(product_id="keyboard_k1_black_us", title="K1 Mechanical Keyboard — Black, US layout", attributes={"layout": "US", "color": "black", "condition": "new", "connectivity": "wired"}),
            merchant_skus={"merchant_a": "K1-BLK-US", "merchant_b": "KB-K1-US-B"},
            quantity=1, currency="USD", price_rule=PriceRule(operator="lt", delivered_total_minor=10000),
            allowed_merchants=["merchant_a", "merchant_b"], destination_id="address_1", expires_at=DEADLINE, timezone="Asia/Kolkata",
            max_purchases=1, authorization_profile="ap2", poll_interval_seconds=60,
        )
        base.update(overrides)
        return WatchDraft(**base)

    def create_active_watch(self, **overrides) -> Dict[str, Any]:
        w = self.service.create("user_demo", self.draft(**overrides))
        self.service.proposal(w)
        w = self.watch(w["id"])
        return self.service.activate(w, w["version"])

    def watch(self, watch_id: str) -> Dict[str, Any]:
        from persistence import repo

        with self.engine.begin() as conn:
            return repo.get_watch(conn, watch_id)

    def set_price(self, merchant_id: str, sku: str, **fields) -> None:
        r = self.merchant_clients[merchant_id].post("/demo/price-scenario", json=dict(sku=sku, **fields))
        assert r.status_code == 200, r.text

    def orders(self, merchant_id: str):
        return self.merchant_clients[merchant_id].get("/demo/orders").json()["orders"]

    def add_adapter_fault(self, fault: str, count: int = 1) -> None:
        from persistence import repo

        with self.engine.begin() as conn:
            repo.add_fault(conn, "adapter", fault, count, self.clock.now())

    def events(self, watch_id: str):
        return self.service.timeline(watch_id)

    def event_types(self, watch_id: str):
        return [e["type"] for e in self.events(watch_id)]

    def tick(self):
        return self.worker.tick()

    def advance(self, **kw):
        return self.clock.advance(**kw)

    def close(self):
        for c in self.merchant_clients.values():
            c.close()
        self.engine.dispose()
        try:
            os.remove(self.db_path)
        except OSError:
            pass
