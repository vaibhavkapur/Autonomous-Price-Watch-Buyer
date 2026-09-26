"""All-in-one development server (no Docker required).

Runs the API, both UCP merchant fixtures and a worker thread in one process on
top of a SQLite database, with a **simulated clock** (labelled in the UI) so the
accelerated demo can be driven from the browser:

    python apps/dev_server.py            # http://localhost:8000/docs

Merchant fixtures are reachable under /merchants/<id>/… for inspection.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone

import uvicorn
from fastapi import FastAPI
from starlette.testclient import TestClient

from api.main import create_api
from authorization_profiles.keys import KeyRing
from common.clock import ManualClock, SystemClock
from common.config import load_settings
from merchant_fixture import MerchantConfig, MerchantState, create_merchant_app
from persistence import make_engine
from product_identity import load_merchant_catalog
from purchase_coordinator import Worker
from purchase_coordinator.bootstrap import build_context
from ucp_adapter import MerchantEndpoint
from watch_domain.service import WatchService


def build() -> FastAPI:  # pragma: no cover - dev entry point
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = load_settings()
    simulated = os.environ.get("DEV_SIMULATED_CLOCK", "1") == "1"
    clock = ManualClock(datetime.now(timezone.utc)) if simulated else SystemClock()
    keys = KeyRing.load_or_create(settings.keys_path)
    trust = keys.trust_store()
    root = FastAPI(title="Autonomous Price-Watch Buyer — dev server")
    endpoints = {}
    catalogs = {}
    for mid in settings.merchant_base_urls:
        catalog = load_merchant_catalog(mid)
        catalogs[mid] = catalog
        base = "http://%s.local" % mid
        cfg = MerchantConfig(mid, catalog["name"], catalog["website"], catalog, keys.private(mid), trust, base_url=base, clock=clock)
        app = create_merchant_app(cfg, MerchantState(cfg))
        root.mount("/merchants/%s" % mid, app)
        endpoints[mid] = MerchantEndpoint(mid, base, TestClient(app, base_url=base))
    engine = make_engine(settings.database_url)
    ctx = build_context(settings, clock=clock, worker_id="dev-worker", endpoints=endpoints, engine=engine, keys=keys)
    ctx.merchant_names = {mid: c["name"] for mid, c in catalogs.items()}
    service = WatchService(ctx, catalogs)
    api = create_api(ctx, service)
    worker = Worker(ctx)
    api.state.worker = worker

    def loop():
        worker.start()
        while True:
            try:
                worker.tick()
            except Exception:
                logging.getLogger("dev").exception("tick failed")
            time.sleep(float(os.environ.get("WORKER_TICK_SECONDS", "2")))

    threading.Thread(target=loop, name="worker", daemon=True).start()
    root.mount("/", api)
    return root


if __name__ == "__main__":  # pragma: no cover
    uvicorn.run(build(), host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
