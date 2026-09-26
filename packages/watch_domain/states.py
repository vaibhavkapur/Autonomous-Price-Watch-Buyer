"""Watch lifecycle state machine (plan §16).

    draft → awaiting_authorization → watching
    watching → paused → watching
    watching → candidate_found → checkout_validating → purchase_claimed
    checkout_validating → watching            [offer no longer qualifies]
    purchase_claimed → submitting → purchased
    submitting → reconciliation_required → purchased | resolved_not_purchased
    watching | paused → cancelled | expired

Authorization and payment state are tracked separately from watch status.
"""
from __future__ import annotations

from enum import Enum
from typing import Dict, FrozenSet


class WatchStatus(str, Enum):
    DRAFT = "draft"
    AWAITING_AUTHORIZATION = "awaiting_authorization"
    WATCHING = "watching"
    PAUSED = "paused"
    CANDIDATE_FOUND = "candidate_found"
    CHECKOUT_VALIDATING = "checkout_validating"
    PURCHASE_CLAIMED = "purchase_claimed"
    SUBMITTING = "submitting"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    PURCHASED = "purchased"
    RESOLVED_NOT_PURCHASED = "resolved_not_purchased"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


S = WatchStatus

TRANSITIONS: Dict[WatchStatus, FrozenSet[WatchStatus]] = {
    S.DRAFT: frozenset({S.AWAITING_AUTHORIZATION, S.CANCELLED}),
    S.AWAITING_AUTHORIZATION: frozenset({S.WATCHING, S.CANCELLED, S.EXPIRED, S.DRAFT}),
    S.WATCHING: frozenset({S.PAUSED, S.CANDIDATE_FOUND, S.CANCELLED, S.EXPIRED}),
    S.PAUSED: frozenset({S.WATCHING, S.CANCELLED, S.EXPIRED}),
    S.CANDIDATE_FOUND: frozenset({S.CHECKOUT_VALIDATING, S.WATCHING, S.CANCELLED, S.EXPIRED}),
    S.CHECKOUT_VALIDATING: frozenset({S.PURCHASE_CLAIMED, S.WATCHING, S.CANCELLED, S.EXPIRED}),
    S.PURCHASE_CLAIMED: frozenset({S.SUBMITTING, S.WATCHING, S.CANCELLED, S.EXPIRED}),
    S.SUBMITTING: frozenset({S.PURCHASED, S.RECONCILIATION_REQUIRED, S.WATCHING}),
    S.RECONCILIATION_REQUIRED: frozenset({S.PURCHASED, S.RESOLVED_NOT_PURCHASED}),
    S.PURCHASED: frozenset(),
    S.RESOLVED_NOT_PURCHASED: frozenset(),
    S.CANCELLED: frozenset(),
    S.EXPIRED: frozenset(),
}

# Statuses in which the scheduler may claim a watch for evaluation.
SCHEDULABLE_STATUSES = frozenset({S.WATCHING})
# Statuses in which the watch is "live" and can expire or be cancelled by the user.
ACTIVE_STATUSES = frozenset({S.AWAITING_AUTHORIZATION, S.WATCHING, S.PAUSED, S.CANDIDATE_FOUND, S.CHECKOUT_VALIDATING})
# Statuses where an execution is in flight; cancellation stops future activity but cannot recall a submission.
IN_FLIGHT_STATUSES = frozenset({S.PURCHASE_CLAIMED, S.SUBMITTING, S.RECONCILIATION_REQUIRED})
TERMINAL_STATUSES = frozenset({S.PURCHASED, S.RESOLVED_NOT_PURCHASED, S.CANCELLED, S.EXPIRED})


class IllegalTransition(Exception):
    pass


def transition(current: str, new: str) -> str:
    cur = WatchStatus(current)
    nxt = WatchStatus(new)
    if nxt not in TRANSITIONS[cur]:
        raise IllegalTransition("%s → %s is not allowed" % (cur.value, nxt.value))
    return nxt.value
