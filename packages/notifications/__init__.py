"""Notification service with a deduplication identity per (watch, event).

The development channel writes to the ``notifications`` table and the process
log only. No real messages are sent while developing this plan.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, Optional

from sqlalchemy.engine import Connection

from common.util import new_id
from persistence import repo

log = logging.getLogger("pricewatch.notifications")


class Notifier:
    def __init__(self, channel: str = "log"):
        self.channel = channel

    def notify(self, conn: Connection, *, watch: Dict, event: str, subject: str, body: str, at: datetime, extra_key: Optional[str] = None) -> bool:
        dedupe = "%s:%s" % (watch["id"], event if extra_key is None else "%s:%s" % (event, extra_key))
        inserted = repo.insert_notification(
            conn,
            {
                "id": new_id("ntf"),
                "watch_id": watch["id"],
                "user_id": watch["user_id"],
                "dedupe_key": dedupe,
                "channel": self.channel,
                "subject": subject,
                "body": body,
                "state": "delivered" if self.channel == "log" else "queued",
                "created_at": at,
                "delivered_at": at if self.channel == "log" else None,
            },
        )
        if inserted:
            log.info("[notify %s] %s — %s", watch["user_id"], subject, body)
        return inserted
