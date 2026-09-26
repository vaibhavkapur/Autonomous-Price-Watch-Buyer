"""Database-backed durable scheduler (plan §11).

* ``next_check_at`` + ``schedule_version`` live on the watch row.
* Workers claim due watches with a lease (``lease_token`` = fencing token).
* Bounded polling interval with deterministic jitter; exponential backoff after
  merchant errors; per-merchant rate limits and backoff windows; no catch-up
  storm after downtime (a late watch is checked once, then rescheduled from *now*).
* Expiry is decided with the server clock, never from queue timestamps.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.engine import Engine

from common.clock import Clock
from common.util import random_token
from persistence import repo
from watch_domain.states import ACTIVE_STATUSES, SCHEDULABLE_STATUSES, WatchStatus


class Scheduler:
    def __init__(self, engine: Engine, clock: Clock, *, worker_id: str, lease_seconds: int = 120, jitter_seconds: int = 10, max_backoff_seconds: int = 1800, merchant_min_interval_ms: int = 0, merchant_backoff_base_seconds: int = 30, merchant_backoff_max_seconds: int = 1800, seed: Optional[int] = None):
        self.engine = engine
        self.clock = clock
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.jitter_seconds = jitter_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self.merchant_min_interval = timedelta(milliseconds=merchant_min_interval_ms)
        self.merchant_backoff_base = merchant_backoff_base_seconds
        self.merchant_backoff_max = merchant_backoff_max_seconds
        self.rng = random.Random(seed)

    # ------------------------------------------------------------ expiry
    def expire_due(self, notifier=None) -> List[str]:
        """Mark active watches whose deadline passed as expired. Watches with an
        in-flight execution are left alone: the coordinator re-checks expiry itself."""
        now = self.clock.now()
        expired: List[str] = []
        with self.engine.begin() as conn:
            for w in repo.expired_active_watches(conn, now, [s.value for s in ACTIVE_STATUSES]):
                ok = repo.update_watch_versioned(conn, w["id"], w["version"], {"status": WatchStatus.EXPIRED.value, "next_check_at": None, "lease_token": None, "lease_owner": None, "lease_expires_at": None}, now=now)
                if ok:
                    repo.add_event(conn, w["id"], now, "watch.expired", self.worker_id, {"expires_at": w["expires_at"].isoformat(), "previous_status": w["status"]}, w["version"] + 1)
                    for a in repo.list_authorizations(conn, w["id"]):
                        if a["status"] == "active":
                            repo.update_authorization(conn, a["id"], status="expired")
                    if notifier:
                        notifier.notify(conn, watch=w, event="expired", subject="Watch expired without a purchase", body="The deadline %s passed; no qualifying offer was purchased." % w["expires_at"].isoformat(), at=now)
                    expired.append(w["id"])
        return expired

    # ------------------------------------------------------------ leases
    def claim_due(self, limit: int = 20) -> List[Tuple[Dict[str, Any], str]]:
        """Return (watch, lease_token) pairs this worker now owns."""
        now = self.clock.now()
        claimed: List[Tuple[Dict[str, Any], str]] = []
        with self.engine.begin() as conn:
            candidates = repo.due_watches(conn, now, [s.value for s in SCHEDULABLE_STATUSES], limit=limit)
        for w in candidates:
            token = random_token(16)
            with self.engine.begin() as conn:
                if repo.try_acquire_lease(conn, w, token, self.worker_id, now, self.lease_seconds):
                    fresh = repo.get_watch(conn, w["id"])
                    claimed.append((fresh, token))
        return claimed

    def release(self, watch_id: str, token: str) -> None:
        with self.engine.begin() as conn:
            repo.release_lease(conn, watch_id, token)

    # --------------------------------------------------------- reschedule
    def next_interval(self, base_seconds: int, consecutive_errors: int) -> timedelta:
        backoff = min(base_seconds * (2 ** min(consecutive_errors, 10)), self.max_backoff_seconds) if consecutive_errors else base_seconds
        jitter = self.rng.uniform(0, self.jitter_seconds) if self.jitter_seconds else 0
        return timedelta(seconds=backoff + jitter)

    def reschedule(self, conn, watch: Dict[str, Any], token: str, *, error: bool = False) -> Optional[datetime]:
        """Release the lease and set the next check, only if the watch is still watching.
        Uses the fencing token so a stale worker cannot overwrite a newer version."""
        now = self.clock.now()
        current = repo.get_watch(conn, watch["id"])
        if current is None or current["status"] != WatchStatus.WATCHING.value or current["lease_token"] != token:
            return None
        errors = current["consecutive_errors"] + 1 if error else 0
        nxt = now + self.next_interval(current["poll_interval_seconds"], errors)
        if nxt >= current["expires_at"]:
            nxt = current["expires_at"]  # one final check exactly at expiry → will be marked expired
        repo.update_watch_versioned(
            conn,
            watch["id"],
            current["version"],
            {"next_check_at": nxt, "consecutive_errors": errors, "lease_token": None, "lease_owner": None, "lease_expires_at": None, "schedule_version": current["schedule_version"] + 1},
            lease_token=token,
            bump_version=False,
            now=now,
        )
        return nxt

    # ---------------------------------------------------- merchant limits
    def merchant_allowed(self, conn, merchant_id: str) -> Tuple[bool, Optional[str]]:
        now = self.clock.now()
        h = repo.get_merchant_health(conn, merchant_id)
        if h.get("backoff_until") and h["backoff_until"] > now:
            return False, "merchant_backoff_until:%s" % h["backoff_until"].isoformat()
        if h.get("last_call_at") and self.merchant_min_interval and now - h["last_call_at"] < self.merchant_min_interval:
            return False, "merchant_rate_limited"
        repo.set_merchant_health(conn, merchant_id, last_call_at=now)
        return True, None

    def merchant_result(self, conn, merchant_id: str, ok: bool, error: Optional[str] = None) -> None:
        now = self.clock.now()
        h = repo.get_merchant_health(conn, merchant_id)
        if ok:
            repo.set_merchant_health(conn, merchant_id, consecutive_errors=0, backoff_until=None, last_error=None)
        else:
            n = h["consecutive_errors"] + 1
            wait = min(self.merchant_backoff_base * (2 ** min(n - 1, 8)), self.merchant_backoff_max)
            repo.set_merchant_health(conn, merchant_id, consecutive_errors=n, backoff_until=now + timedelta(seconds=wait), last_error=(error or "")[:500])
