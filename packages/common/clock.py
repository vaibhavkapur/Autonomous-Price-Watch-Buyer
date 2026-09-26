"""Injectable clocks.

Every time comparison in the system goes through a ``Clock`` so that tests and
the accelerated demo can drive time deterministically. All instants are
timezone-aware UTC ``datetime`` objects; persistence stores naive UTC.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Optional


def utc(dt: datetime) -> datetime:
    """Normalise a datetime to aware-UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_naive_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return utc(dt).replace(tzinfo=None)


def from_naive_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc)


def parse_iso(value: str) -> datetime:
    """Parse an RFC 3339 / ISO-8601 timestamp (accepts trailing Z)."""
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError("timestamp must carry a time zone: %r" % value)
    return utc(dt)


def iso(dt: datetime) -> str:
    return utc(dt).isoformat().replace("+00:00", "Z")


class Clock:
    def now(self) -> datetime:  # pragma: no cover - interface
        raise NotImplementedError

    def epoch(self) -> int:
        return int(self.now().timestamp())


class SystemClock(Clock):
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class ManualClock(Clock):
    """A clock that only moves when told to. Thread-safe."""

    def __init__(self, start: datetime):
        self._now = utc(start)
        self._lock = threading.Lock()

    def now(self) -> datetime:
        with self._lock:
            return self._now

    def set(self, value: datetime) -> None:
        with self._lock:
            self._now = utc(value)

    def advance(self, **kwargs) -> datetime:
        with self._lock:
            self._now = self._now + timedelta(**kwargs)
            return self._now
