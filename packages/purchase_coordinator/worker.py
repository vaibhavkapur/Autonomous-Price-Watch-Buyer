"""Durable worker loop: recover → expire → reconcile → claim due → evaluate."""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List

from .context import Context
from .coordinator import PurchaseCoordinator
from .evaluation import evaluate_watch

log = logging.getLogger("pricewatch.worker")


class Worker:
    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.coordinator = PurchaseCoordinator(ctx)
        self.recovered = False

    def start(self) -> Dict[str, int]:
        """Recover durable state from the database (plan §18: scheduler restarts)."""
        summary = self.coordinator.recover()
        self.recovered = True
        log.info("worker %s recovered: %s", self.ctx.worker_id, summary)
        return summary

    def tick(self, limit: int = 20) -> List[Dict[str, Any]]:
        if not self.recovered:
            self.start()
        results: List[Dict[str, Any]] = []
        expired = self.ctx.scheduler.expire_due(self.ctx.notifier)
        for wid in expired:
            results.append({"watch_id": wid, "outcome": "expired"})
        self.coordinator.reconcile_open()
        for watch, token in self.ctx.scheduler.claim_due(limit=limit):
            try:
                out = evaluate_watch(self.ctx, watch, token)
            except Exception as e:  # pragma: no cover - defensive: never let one watch kill the loop
                log.exception("evaluation of %s failed", watch["id"])
                out = {"outcome": "error", "reason": repr(e)}
                self.ctx.scheduler.release(watch["id"], token)
            out["watch_id"] = watch["id"]
            results.append(out)
        return results

    def run_forever(self, sleep_seconds: float = 2.0) -> None:  # pragma: no cover - process entry point
        self.start()
        while True:
            try:
                self.tick()
            except Exception:
                log.exception("tick failed")
            time.sleep(sleep_seconds)
