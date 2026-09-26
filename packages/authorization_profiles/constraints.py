"""Constraint evaluators shared (structurally) by the two profiles.

Each profile maps its own constraint ``type`` strings onto these functions; the
type vocabularies differ (AP2: ``checkout.*``/``payment.*``; VI:
``mandate.checkout.*``/``mandate.payment.*``) and are never mixed.
"""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from common.clock import parse_iso


def merchant_matches(candidate: Dict[str, Any], allowed: Dict[str, Any]) -> bool:
    if candidate.get("id") and allowed.get("id"):
        return candidate["id"] == allowed["id"]
    return candidate.get("name") == allowed.get("name") and candidate.get("website") == allowed.get("website")


def eval_allowed_merchants(allowed: List[Dict[str, Any]], merchant: Dict[str, Any]) -> Tuple[bool, str]:
    revealed = [m for m in allowed if isinstance(m, dict) and "..." not in m]
    if not revealed:
        return False, "allowed_merchants: no revealed merchant entries"
    if any(merchant_matches(merchant, a) for a in revealed):
        return True, "merchant %s is in the revealed allowlist" % merchant.get("id")
    return False, "merchant %s not in revealed allowlist" % merchant.get("id")


def _max_flow(capacity: Dict[str, Dict[str, int]], source: str, sink: str) -> int:
    flow = 0
    while True:
        parent: Dict[str, Optional[str]] = {source: None}
        q = deque([source])
        while q and sink not in parent:
            u = q.popleft()
            for v, cap in capacity.get(u, {}).items():
                if cap > 0 and v not in parent:
                    parent[v] = u
                    q.append(v)
        if sink not in parent:
            return flow
        # find bottleneck
        path_flow = 1 << 60
        v = sink
        while parent[v] is not None:
            u = parent[v]
            path_flow = min(path_flow, capacity[u][v])
            v = u
        v = sink
        while parent[v] is not None:
            u = parent[v]
            capacity[u][v] -= path_flow
            capacity.setdefault(v, {})
            capacity[v][u] = capacity[v].get(u, 0) + path_flow
            v = u
        flow += path_flow


def eval_line_items(entries: List[Dict[str, Any]], checkout_line_items: List[Dict[str, Any]], *, exact: bool = True) -> Tuple[bool, str]:
    """Maximal-flow evaluation (AP2 §Line Items). ``exact`` requires that the
    checkout contains exactly the constrained quantities and nothing else."""
    if not entries:
        return False, "line_items: empty constraint is unsatisfiable"
    cart: Dict[str, int] = defaultdict(int)
    for li in checkout_line_items:
        item_id = (li.get("item") or {}).get("id") or li.get("id") or li.get("sku")
        cart[item_id] += int(li.get("quantity", 0))
    if not cart:
        return False, "line_items: empty cart"
    cap: Dict[str, Dict[str, int]] = defaultdict(dict)
    need = 0
    for e in entries:
        node = "entry:%s" % e["id"]
        q = int(e["quantity"])
        need += q
        cap["src"][node] = q
        acceptable = [a for a in e.get("acceptable_items", []) if isinstance(a, dict) and "..." not in a]
        for a in acceptable:
            if a["id"] in cart:
                cap[node]["item:%s" % a["id"]] = 1 << 40
    have = 0
    for item_id, qty in cart.items():
        cap["item:%s" % item_id]["sink"] = qty
        have += qty
    flow = _max_flow(cap, "src", "sink")
    if flow != need:
        return False, "line_items: only %d of %d required units can be matched to acceptable items" % (flow, need)
    if exact and flow != have:
        return False, "line_items: checkout contains %d units but only %d are authorised" % (have, need)
    return True, "line_items: all %d unit(s) matched" % need


def eval_amount_range(c: Dict[str, Any], amount: Dict[str, Any]) -> Tuple[bool, str]:
    if not isinstance(amount.get("amount"), int) or isinstance(amount.get("amount"), bool):
        return False, "amount_range: payment_amount.amount must be an integer in minor units"
    if c.get("currency") != amount.get("currency"):
        return False, "amount_range: currency mismatch"
    a = amount["amount"]
    lo = c.get("min", None)
    hi = c.get("max")
    if not isinstance(hi, int):
        return False, "amount_range: max must be an integer in minor units"
    if lo is not None and a < lo:
        return False, "amount_range: %d below minimum %d" % (a, lo)
    if a > hi:
        return False, "amount_range: %d exceeds maximum %d" % (a, hi)
    return True, "amount_range: %d within [%s, %d]" % (a, lo, hi)


def eval_allowed_payees(allowed: List[Dict[str, Any]], payee: Dict[str, Any]) -> Tuple[bool, str]:
    revealed = [m for m in allowed if isinstance(m, dict) and "..." not in m]
    if not revealed:
        return False, "allowed_payees: no revealed payee entries"
    if any(merchant_matches(payee, a) for a in revealed):
        return True, "payee %s is in the revealed allowlist" % payee.get("id")
    return False, "payee %s not in revealed allowlist" % payee.get("id")


def eval_execution_date(c: Dict[str, Any], execution_date: Optional[str], now: datetime) -> Tuple[bool, str]:
    when = parse_iso(execution_date) if execution_date else now
    nb = c.get("not_before")
    na = c.get("not_after")
    if nb and when < parse_iso(nb):
        return False, "execution_date: %s before not_before %s" % (when.isoformat(), nb)
    if na and when > parse_iso(na):
        return False, "execution_date: %s after not_after %s" % (when.isoformat(), na)
    return True, "execution_date: within window"
