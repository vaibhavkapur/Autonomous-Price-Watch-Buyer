"""Environment-driven configuration shared by the API, worker and fixtures."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[2]


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    profile: str = field(default_factory=lambda: _env("APP_PROFILE", "development"))
    database_url: str = field(
        default_factory=lambda: _env("DATABASE_URL", "sqlite:///%s" % (ROOT / "data" / "pricewatch.sqlite3"))
    )
    # Demo/test controls must be disabled outside the development profile.
    demo_controls_enabled: bool = field(default_factory=lambda: _env_bool("DEMO_CONTROLS", True))
    poll_interval_seconds: int = field(default_factory=lambda: int(_env("POLL_INTERVAL_SECONDS", "60")))
    poll_jitter_seconds: int = field(default_factory=lambda: int(_env("POLL_JITTER_SECONDS", "10")))
    lease_seconds: int = field(default_factory=lambda: int(_env("LEASE_SECONDS", "120")))
    observation_freshness_seconds: int = field(default_factory=lambda: int(_env("OBSERVATION_FRESHNESS_SECONDS", "300")))
    merchant_min_interval_ms: int = field(default_factory=lambda: int(_env("MERCHANT_MIN_INTERVAL_MS", "500")))
    merchant_backoff_base_seconds: int = field(default_factory=lambda: int(_env("MERCHANT_BACKOFF_BASE_SECONDS", "30")))
    merchant_backoff_max_seconds: int = field(default_factory=lambda: int(_env("MERCHANT_BACKOFF_MAX_SECONDS", "1800")))
    http_timeout_seconds: float = field(default_factory=lambda: float(_env("HTTP_TIMEOUT_SECONDS", "10")))
    keys_path: Path = field(default_factory=lambda: Path(_env("DEV_KEYS_PATH", str(ROOT / "fixtures" / "authorizations" / "dev_keys.json"))))
    artifact_encryption_key: str = field(default_factory=lambda: _env("ARTIFACT_ENCRYPTION_KEY", ""))
    agent_id: str = field(default_factory=lambda: _env("AGENT_ID", "agent_pricewatch_1"))
    agent_platform_url: str = field(default_factory=lambda: _env("AGENT_PLATFORM_URL", "https://agent.pricewatch.local"))
    merchant_base_urls: Dict[str, str] = field(
        default_factory=lambda: {
            "merchant_a": _env("MERCHANT_A_URL", "http://localhost:8101"),
            "merchant_b": _env("MERCHANT_B_URL", "http://localhost:8102"),
        }
    )
    api_cors_origins: List[str] = field(default_factory=lambda: _env("API_CORS_ORIGINS", "http://localhost:3000").split(","))

    @property
    def is_development(self) -> bool:
        return self.profile == "development"

    @property
    def demo_enabled(self) -> bool:
        return self.is_development and self.demo_controls_enabled


def load_settings() -> Settings:
    return Settings()
