"""Durable worker process: recovers state from the database on start, then
polls due watches with bounded cadence. Queue messages (if any) are hints only."""
from __future__ import annotations

import logging
import os
import socket

from purchase_coordinator import Worker
from purchase_coordinator.bootstrap import build_context


def main() -> None:  # pragma: no cover - process entry point
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    worker_id = os.environ.get("WORKER_ID", "worker-%s-%d" % (socket.gethostname(), os.getpid()))
    ctx = build_context(worker_id=worker_id)
    Worker(ctx).run_forever(sleep_seconds=float(os.environ.get("WORKER_TICK_SECONDS", "2")))


if __name__ == "__main__":  # pragma: no cover
    main()
