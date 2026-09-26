"""Build a runtime Context from settings (API and worker processes)."""
from __future__ import annotations

import hashlib
from datetime import timedelta
from typing import Dict, Optional

import httpx
from sqlalchemy.engine import Engine

from authorization_profiles.keys import KeyRing
from authorization_profiles.payment_sim import CredentialProviderSim
from common.clock import Clock, SystemClock
from common.config import Settings, load_settings
from notifications import Notifier
from persistence import init_schema, make_engine, repo
from persistence.artifacts import ArtifactVault
from scheduler import Scheduler
from ucp_adapter import MerchantEndpoint, UCPClient

from .context import Context

DEV_USERS = [
    {"id": "user_demo", "name": "Demo User", "token": "demo-token"},
]
DEV_DESTINATIONS = [
    {"id": "address_1", "user_id": "user_demo", "label": "Home", "address": {"name": "Demo User", "line1": "1 Example Way", "city": "Springfield", "region": "IL", "postal_code": "62701", "country": "US"}},
]


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def seed_dev_data(engine: Engine, keys: KeyRing, agent_id: str) -> None:
    with engine.begin() as conn:
        for u in DEV_USERS:
            repo.upsert_user(conn, u["id"], u["name"], token_hash(u["token"]))
        for d in DEV_DESTINATIONS:
            repo.upsert_destination(conn, d["id"], d["user_id"], d["label"], d["address"])
        repo.upsert_agent(conn, agent_id, "Price-watch shopping agent", keys.kid("agent"), keys.public_jwk("agent"))


def db_fault_hook(engine: Engine):
    def hook(target: str, fault: str) -> bool:
        with engine.begin() as conn:
            return repo.consume_fault(conn, target, fault)

    return hook


def build_context(settings: Optional[Settings] = None, *, clock: Optional[Clock] = None, worker_id: str = "worker-1", endpoints: Optional[Dict[str, MerchantEndpoint]] = None, engine: Optional[Engine] = None, keys: Optional[KeyRing] = None, seed: bool = True) -> Context:
    settings = settings or load_settings()
    clock = clock or SystemClock()
    engine = engine or make_engine(settings.database_url)
    init_schema(engine)
    keys = keys or KeyRing.load_or_create(settings.keys_path)
    trust = keys.trust_store()
    if endpoints is None:
        http = httpx.Client(timeout=settings.http_timeout_seconds)
        endpoints = {mid: MerchantEndpoint(mid, url, http) for mid, url in settings.merchant_base_urls.items()}
    ucp = UCPClient(endpoints, fault_hook=db_fault_hook(engine), timeout=settings.http_timeout_seconds)
    scheduler = Scheduler(
        engine, clock, worker_id=worker_id, lease_seconds=settings.lease_seconds, jitter_seconds=settings.poll_jitter_seconds,
        merchant_min_interval_ms=settings.merchant_min_interval_ms, merchant_backoff_base_seconds=settings.merchant_backoff_base_seconds, merchant_backoff_max_seconds=settings.merchant_backoff_max_seconds,
    )
    ctx = Context(
        engine=engine, clock=clock, settings=settings, ucp=ucp, keys=keys, trust=trust,
        credential_provider=CredentialProviderSim(keys, trust), notifier=Notifier("log"), vault=ArtifactVault(settings.artifact_encryption_key),
        scheduler=scheduler, worker_id=worker_id, freshness=timedelta(seconds=settings.observation_freshness_seconds),
    )
    if seed:
        seed_dev_data(engine, keys, settings.agent_id)
    return ctx
