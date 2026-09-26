"""Natural-language → reviewable constraint fields.

The LLM (when configured) only *proposes* fields; this deterministic parser is
the default and the reference for tests. Nothing here is executable authority:
the user reviews and signs the normalised rule produced by ``normalize_rule``.
"""
from __future__ import annotations

import re
from datetime import datetime, time, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from common.money import parse_display_to_minor

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

_AMOUNT = r"\$?\s*(\d{1,6}(?:\.\d{1,2})?)"
_LT_PATTERNS = [
    r"(?:drops?|falls?|goes?|is|gets?)?\s*(?:strictly\s+)?(?:below|under|less than|cheaper than)\s+" + _AMOUNT,
]
_LTE_PATTERNS = [
    r"(?:at most|no more than|up to|max(?:imum)? of|not exceeding)\s+" + _AMOUNT,
    _AMOUNT + r"\s+or\s+(?:less|lower|under)",
]


def _next_weekday(now_local: datetime, weekday: int, end_of_day: bool = True) -> datetime:
    days_ahead = (weekday - now_local.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    target = (now_local + timedelta(days=days_ahead)).date()
    return datetime.combine(target, time(23, 59, 59) if end_of_day else time(0, 0), tzinfo=now_local.tzinfo)


def parse_request(
    text: str,
    *,
    now: datetime,
    timezone: str = "UTC",
    known_merchants: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Return a partially-filled draft plus ``unknowns``/``notes`` for review."""
    lowered = text.lower()
    tz = ZoneInfo(timezone)
    now_local = now.astimezone(tz)
    notes: List[str] = []
    unknowns: List[str] = []
    result: Dict[str, Any] = {"original_request": text, "currency": "USD", "quantity": 1, "max_purchases": 1, "timezone": timezone}

    # ---- price rule
    operator = None
    amount_minor = None
    for pat in _LTE_PATTERNS:
        m = re.search(pat, lowered)
        if m:
            operator, amount_minor = "lte", parse_display_to_minor(m.group(1))
            break
    if operator is None:
        for pat in _LT_PATTERNS:
            m = re.search(pat, lowered)
            if m:
                operator, amount_minor = "lt", parse_display_to_minor(m.group(1))
                break
    if operator is None:
        unknowns.append("price_rule: could not find a threshold such as 'below $100' or 'at most $95'")
    else:
        result["price_rule"] = {"operator": operator, "delivered_total_minor": amount_minor}
        if operator == "lt":
            notes.append("'below' is read as a strict comparison; the largest qualifying total is %d cents." % (amount_minor - 1))
        if "delivered" not in lowered and "total" not in lowered and "shipped" not in lowered:
            notes.append("The threshold is applied to the delivered total (item + shipping + tax + fees), not the sticker price.")

    # ---- deadline
    deadline: Optional[datetime] = None
    m = re.search(r"(?:before|by|until)\s+(" + "|".join(WEEKDAYS) + r")", lowered)
    if m:
        deadline = _next_weekday(now_local, WEEKDAYS.index(m.group(1)))
        notes.append("'before %s' is read as end of day %s in %s." % (m.group(1).title(), deadline.date().isoformat(), timezone))
    else:
        m = re.search(r"(?:before|by|until)\s+(\d{4}-\d{2}-\d{2})(?:[t ](\d{2}:\d{2}))?", lowered)
        if m:
            d = datetime.fromisoformat(m.group(1)).date()
            t = time.fromisoformat(m.group(2)) if m.group(2) else time(23, 59, 59)
            deadline = datetime.combine(d, t, tzinfo=tz)
        else:
            m = re.search(r"(?:within|for)\s+(?:the\s+)?(?:next\s+)?(\d+)\s+(hour|day|week)s?", lowered)
            if m:
                n = int(m.group(1))
                unit = m.group(2)
                delta = {"hour": timedelta(hours=n), "day": timedelta(days=n), "week": timedelta(weeks=n)}[unit]
                deadline = now_local + delta
    if deadline is None:
        unknowns.append("expires_at: no deadline found (e.g. 'before Sunday', 'by 2026-09-27')")
    else:
        result["expires_at"] = deadline.isoformat()

    # ---- merchants
    merchants: List[str] = []
    if known_merchants:
        for mid, name in known_merchants.items():
            if mid.lower() in lowered or name.lower() in lowered:
                merchants.append(mid)
    if merchants:
        result["allowed_merchants"] = merchants
    elif re.search(r"(these|those|the)\s+(two|2)\s+merchants", lowered) and known_merchants and len(known_merchants) == 2:
        result["allowed_merchants"] = list(known_merchants.keys())
        notes.append("'these two merchants' resolved to the two merchants attached to this request.")
    else:
        unknowns.append("allowed_merchants: name the merchants explicitly")

    # ---- quantity / count
    if re.search(r"\b(twice|two units|2 units|multiple)\b", lowered):
        unknowns.append("max_purchases: only a single purchase is supported in this release")
    if re.search(r"\b(once|one time|a single)\b", lowered):
        notes.append("'buy it once' → max_purchases = 1.")

    # ---- product
    if re.search(r"\b(similar|like|comparable|any)\b", lowered):
        notes.append("Words like 'similar/any' are ignored: only the exact product identity you select is authorised.")
    unknowns.append("product: select the exact catalog product and confirm per-merchant SKUs")

    result["notes"] = notes
    result["unknowns"] = unknowns
    return result
