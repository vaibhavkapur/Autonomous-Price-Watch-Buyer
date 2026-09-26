"""Runtime wiring shared by the worker, the API and the tests."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Dict

from sqlalchemy.engine import Engine

from authorization_profiles.base import AuthorizationProfile
from authorization_profiles.keys import KeyRing, TrustStore
from authorization_profiles.payment_sim import CredentialProviderSim
from authorization_profiles.registry import get_profile
from common.clock import Clock
from common.config import Settings
from notifications import Notifier
from persistence.artifacts import ArtifactVault
from scheduler import Scheduler
from ucp_adapter import UCPClient


@dataclass
class Context:
    engine: Engine
    clock: Clock
    settings: Settings
    ucp: UCPClient
    keys: KeyRing
    trust: TrustStore
    credential_provider: CredentialProviderSim
    notifier: Notifier
    vault: ArtifactVault
    scheduler: Scheduler
    worker_id: str = "worker-1"
    freshness: timedelta = timedelta(seconds=300)
    merchant_names: Dict[str, str] = field(default_factory=dict)

    def profile(self, name: str) -> AuthorizationProfile:
        return get_profile(name, self.keys, self.trust)

    def now(self):
        return self.clock.now()
