"""Thin repository helpers over the Core schema.

All functions take an open ``Connection`` and never commit; the caller owns
the transaction (``with engine.begin() as conn``).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import and_, delete, func, insert, or_, select, update
from sqlalchemy.engine import Connection

from common.clock import from_naive_utc, to_naive_utc
from common.util import new_id

from . import schema as s


def _row(r) -> Optional[Dict[str, Any]]:
    if r is None:
        return None
    d = dict(r._mapping)
    for k, v in list(d.items()):
        if isinstance(v, datetime):
            d[k] = from_naive_utc(v)
    return d


def _rows(rs) -> List[Dict[str, Any]]:
    return [_row(r) for r in rs]


def _n(dt: Optional[datetime]) -> Optional[datetime]:
    return to_naive_utc(dt)


# ---------------------------------------------------------------- users
def upsert_user(conn: Connection, user_id: str, name: str, token_sha256: str) -> None:
    existing = conn.execute(select(s.users.c.id).where(s.users.c.id == user_id)).first()
    if existing:
        conn.execute(update(s.users).where(s.users.c.id == user_id).values(name=name, token_sha256=token_sha256))
    else:
        conn.execute(insert(s.users).values(id=user_id, name=name, token_sha256=token_sha256))


def get_user(conn: Connection, user_id: str) -> Optional[Dict[str, Any]]:
    return _row(conn.execute(select(s.users).where(s.users.c.id == user_id)).first())


def user_by_token(conn: Connection, token_sha256: str) -> Optional[Dict[str, Any]]:
    return _row(conn.execute(select(s.users).where(s.users.c.token_sha256 == token_sha256)).first())


def upsert_destination(conn: Connection, dest_id: str, user_id: str, label: str, address: Dict[str, Any]) -> None:
    existing = conn.execute(select(s.destinations.c.id).where(s.destinations.c.id == dest_id)).first()
    if existing:
        conn.execute(update(s.destinations).where(s.destinations.c.id == dest_id).values(user_id=user_id, label=label, address=address))
    else:
        conn.execute(insert(s.destinations).values(id=dest_id, user_id=user_id, label=label, address=address))


def get_destination(conn: Connection, dest_id: str) -> Optional[Dict[str, Any]]:
    return _row(conn.execute(select(s.destinations).where(s.destinations.c.id == dest_id)).first())


def list_destinations(conn: Connection, user_id: str) -> List[Dict[str, Any]]:
    return _rows(conn.execute(select(s.destinations).where(s.destinations.c.user_id == user_id)))


def upsert_agent(conn: Connection, agent_id: str, name: str, key_id: str, public_jwk: Dict[str, Any]) -> None:
    existing = conn.execute(select(s.agents.c.id).where(s.agents.c.id == agent_id)).first()
    if existing:
        conn.execute(update(s.agents).where(s.agents.c.id == agent_id).values(name=name, key_id=key_id, public_jwk=public_jwk))
    else:
        conn.execute(insert(s.agents).values(id=agent_id, name=name, key_id=key_id, public_jwk=public_jwk))


# -------------------------------------------------------------- watches
def insert_watch(conn: Connection, values: Dict[str, Any]) -> None:
    v = dict(values)
    for k in ("expires_at", "next_check_at", "created_at", "updated_at", "cancelled_at", "lease_expires_at"):
        if k in v:
            v[k] = _n(v[k])
    conn.execute(insert(s.watches).values(**v))


def get_watch(conn: Connection, watch_id: str) -> Optional[Dict[str, Any]]:
    return _row(conn.execute(select(s.watches).where(s.watches.c.id == watch_id)).first())


def list_watches(conn: Connection, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    q = select(s.watches).order_by(s.watches.c.created_at.desc())
    if user_id:
        q = q.where(s.watches.c.user_id == user_id)
    return _rows(conn.execute(q))


def update_watch_versioned(
    conn: Connection,
    watch_id: str,
    expected_version: int,
    values: Dict[str, Any],
    *,
    lease_token: Optional[str] = None,
    bump_version: bool = True,
    now: Optional[datetime] = None,
) -> bool:
    """Conditional update. Returns False if the version (or lease) no longer matches."""
    v = dict(values)
    for k in ("expires_at", "next_check_at", "updated_at", "cancelled_at", "lease_expires_at"):
        if k in v:
            v[k] = _n(v[k])
    if now is not None:
        v["updated_at"] = _n(now)
    if bump_version:
        v["version"] = expected_version + 1
    cond = and_(s.watches.c.id == watch_id, s.watches.c.version == expected_version)
    if lease_token is not None:
        cond = and_(cond, s.watches.c.lease_token == lease_token)
    res = conn.execute(update(s.watches).where(cond).values(**v))
    return res.rowcount == 1


def due_watches(conn: Connection, now: datetime, statuses: Iterable[str], limit: int = 50) -> List[Dict[str, Any]]:
    n = _n(now)
    q = (
        select(s.watches)
        .where(
            and_(
                s.watches.c.status.in_(list(statuses)),
                s.watches.c.next_check_at.isnot(None),
                s.watches.c.next_check_at <= n,
                or_(s.watches.c.lease_token.is_(None), s.watches.c.lease_expires_at < n),
            )
        )
        .order_by(s.watches.c.next_check_at.asc())
        .limit(limit)
    )
    return _rows(conn.execute(q))


def try_acquire_lease(conn: Connection, watch: Dict[str, Any], token: str, owner: str, now: datetime, lease_seconds: int) -> bool:
    n = _n(now)
    from datetime import timedelta

    res = conn.execute(
        update(s.watches)
        .where(
            and_(
                s.watches.c.id == watch["id"],
                s.watches.c.version == watch["version"],
                or_(s.watches.c.lease_token.is_(None), s.watches.c.lease_expires_at < n),
            )
        )
        .values(lease_token=token, lease_owner=owner, lease_expires_at=n + timedelta(seconds=lease_seconds))
    )
    return res.rowcount == 1


def release_lease(conn: Connection, watch_id: str, token: str) -> bool:
    res = conn.execute(
        update(s.watches)
        .where(and_(s.watches.c.id == watch_id, s.watches.c.lease_token == token))
        .values(lease_token=None, lease_owner=None, lease_expires_at=None)
    )
    return res.rowcount == 1


def expired_active_watches(conn: Connection, now: datetime, statuses: Iterable[str]) -> List[Dict[str, Any]]:
    q = select(s.watches).where(and_(s.watches.c.status.in_(list(statuses)), s.watches.c.expires_at <= _n(now)))
    return _rows(conn.execute(q))


# --------------------------------------------------------------- events
def add_event(conn: Connection, watch_id: str, at: datetime, type_: str, actor: str, details: Dict[str, Any], watch_version: Optional[int] = None) -> None:
    conn.execute(
        insert(s.watch_events).values(watch_id=watch_id, at=_n(at), type=type_, actor=actor, watch_version=watch_version, details=details)
    )


def list_events(conn: Connection, watch_id: str) -> List[Dict[str, Any]]:
    return _rows(conn.execute(select(s.watch_events).where(s.watch_events.c.watch_id == watch_id).order_by(s.watch_events.c.id.asc())))


# ------------------------------------------------------- authorizations
def insert_authorization(conn: Connection, values: Dict[str, Any]) -> None:
    v = dict(values)
    v["expires_at"] = _n(v["expires_at"])
    v["created_at"] = _n(v["created_at"])
    conn.execute(insert(s.authorizations).values(**v))


def active_authorization(conn: Connection, watch_id: str) -> Optional[Dict[str, Any]]:
    q = (
        select(s.authorizations)
        .where(and_(s.authorizations.c.watch_id == watch_id, s.authorizations.c.status == "active"))
        .order_by(s.authorizations.c.created_at.desc())
    )
    return _row(conn.execute(q).first())


def list_authorizations(conn: Connection, watch_id: str) -> List[Dict[str, Any]]:
    return _rows(conn.execute(select(s.authorizations).where(s.authorizations.c.watch_id == watch_id).order_by(s.authorizations.c.created_at.asc())))


def update_authorization(conn: Connection, auth_id: str, **values) -> None:
    conn.execute(update(s.authorizations).where(s.authorizations.c.id == auth_id).values(**values))


def set_presentation_state(conn: Connection, auth_id: str, expected: Iterable[str], new_state: str, attempt_id: Optional[str]) -> bool:
    res = conn.execute(
        update(s.authorizations)
        .where(and_(s.authorizations.c.id == auth_id, s.authorizations.c.presentation_state.in_(list(expected))))
        .values(presentation_state=new_state, pending_attempt_id=attempt_id)
    )
    return res.rowcount == 1


# ------------------------------------------------------------ artifacts
def insert_artifact(conn: Connection, kind: str, digest: str, ciphertext: str, at: datetime, *, watch_id=None, authorization_id=None, attempt_id=None) -> str:
    art_id = new_id("art")
    conn.execute(
        insert(s.protocol_artifacts).values(
            id=art_id, kind=kind, watch_id=watch_id, authorization_id=authorization_id, attempt_id=attempt_id, digest=digest, ciphertext=ciphertext, created_at=_n(at)
        )
    )
    return art_id


def list_artifacts(conn: Connection, *, watch_id: Optional[str] = None, attempt_id: Optional[str] = None, authorization_id: Optional[str] = None) -> List[Dict[str, Any]]:
    q = select(s.protocol_artifacts)
    if watch_id:
        q = q.where(s.protocol_artifacts.c.watch_id == watch_id)
    if attempt_id:
        q = q.where(s.protocol_artifacts.c.attempt_id == attempt_id)
    if authorization_id:
        q = q.where(s.protocol_artifacts.c.authorization_id == authorization_id)
    return _rows(conn.execute(q.order_by(s.protocol_artifacts.c.created_at.asc())))


def get_artifact(conn: Connection, art_id: str) -> Optional[Dict[str, Any]]:
    return _row(conn.execute(select(s.protocol_artifacts).where(s.protocol_artifacts.c.id == art_id)).first())


# --------------------------------------------------------- observations
def insert_observation(conn: Connection, values: Dict[str, Any]) -> None:
    v = dict(values)
    v["observed_at"] = _n(v["observed_at"])
    v["valid_until"] = _n(v.get("valid_until"))
    conn.execute(insert(s.offer_observations).values(**v))


def list_observations(conn: Connection, watch_id: str, limit: int = 100) -> List[Dict[str, Any]]:
    q = select(s.offer_observations).where(s.offer_observations.c.watch_id == watch_id).order_by(s.offer_observations.c.observed_at.desc()).limit(limit)
    return _rows(conn.execute(q))


def update_observation(conn: Connection, obs_id: str, **values) -> None:
    conn.execute(update(s.offer_observations).where(s.offer_observations.c.id == obs_id).values(**values))


# ------------------------------------------------------ evaluation runs
def insert_run(conn: Connection, values: Dict[str, Any]) -> None:
    v = dict(values)
    v["started_at"] = _n(v["started_at"])
    conn.execute(insert(s.evaluation_runs).values(**v))


def finish_run(conn: Connection, run_id: str, at: datetime, outcome: str, details: Dict[str, Any], selected_offer_id: Optional[str] = None) -> None:
    conn.execute(
        update(s.evaluation_runs)
        .where(s.evaluation_runs.c.id == run_id)
        .values(finished_at=_n(at), outcome=outcome, details=details, selected_offer_id=selected_offer_id)
    )


def list_runs(conn: Connection, watch_id: str) -> List[Dict[str, Any]]:
    return _rows(conn.execute(select(s.evaluation_runs).where(s.evaluation_runs.c.watch_id == watch_id).order_by(s.evaluation_runs.c.started_at.asc())))


# ---------------------------------------------------- purchase attempts
def insert_attempt(conn: Connection, values: Dict[str, Any]) -> None:
    v = dict(values)
    v["claimed_at"] = _n(v["claimed_at"])
    conn.execute(insert(s.purchase_attempts).values(**v))


def get_attempt(conn: Connection, attempt_id: str) -> Optional[Dict[str, Any]]:
    return _row(conn.execute(select(s.purchase_attempts).where(s.purchase_attempts.c.id == attempt_id)).first())


def attempts_for_watch(conn: Connection, watch_id: str) -> List[Dict[str, Any]]:
    return _rows(conn.execute(select(s.purchase_attempts).where(s.purchase_attempts.c.watch_id == watch_id).order_by(s.purchase_attempts.c.claimed_at.asc())))


def active_attempt(conn: Connection, watch_id: str) -> Optional[Dict[str, Any]]:
    q = select(s.purchase_attempts).where(
        and_(s.purchase_attempts.c.watch_id == watch_id, s.purchase_attempts.c.claim_state.in_(list(s.ACTIVE_CLAIM_STATES)))
    )
    return _row(conn.execute(q).first())


def attempts_in_states(conn: Connection, states: Iterable[str]) -> List[Dict[str, Any]]:
    return _rows(conn.execute(select(s.purchase_attempts).where(s.purchase_attempts.c.claim_state.in_(list(states)))))


def update_attempt_state(conn: Connection, attempt_id: str, expected_states: Iterable[str], **values) -> bool:
    v = dict(values)
    for k in ("submitted_at", "resolved_at"):
        if k in v:
            v[k] = _n(v[k])
    res = conn.execute(
        update(s.purchase_attempts)
        .where(and_(s.purchase_attempts.c.id == attempt_id, s.purchase_attempts.c.claim_state.in_(list(expected_states))))
        .values(**v)
    )
    return res.rowcount == 1


# ------------------------------------------------------ outbound events
def insert_outbound(conn: Connection, values: Dict[str, Any]) -> None:
    v = dict(values)
    v["created_at"] = _n(v["created_at"])
    conn.execute(insert(s.outbound_events).values(**v))


def update_outbound(conn: Connection, event_id: str, **values) -> None:
    v = dict(values)
    for k in ("sent_at", "acknowledged_at"):
        if k in v:
            v[k] = _n(v[k])
    conn.execute(update(s.outbound_events).where(s.outbound_events.c.id == event_id).values(**v))


def outbound_for_attempt(conn: Connection, attempt_id: str) -> List[Dict[str, Any]]:
    return _rows(conn.execute(select(s.outbound_events).where(s.outbound_events.c.attempt_id == attempt_id).order_by(s.outbound_events.c.created_at.asc())))


# -------------------------------------------------------- notifications
def insert_notification(conn: Connection, values: Dict[str, Any]) -> bool:
    existing = conn.execute(select(s.notifications.c.id).where(s.notifications.c.dedupe_key == values["dedupe_key"])).first()
    if existing:
        return False
    v = dict(values)
    v["created_at"] = _n(v["created_at"])
    v["delivered_at"] = _n(v.get("delivered_at"))
    conn.execute(insert(s.notifications).values(**v))
    return True


def list_notifications(conn: Connection, watch_id: Optional[str] = None) -> List[Dict[str, Any]]:
    q = select(s.notifications).order_by(s.notifications.c.created_at.asc())
    if watch_id:
        q = q.where(s.notifications.c.watch_id == watch_id)
    return _rows(conn.execute(q))


# ------------------------------------------------- reconciliation cases
def insert_case(conn: Connection, values: Dict[str, Any]) -> None:
    v = dict(values)
    v["opened_at"] = _n(v["opened_at"])
    conn.execute(insert(s.reconciliation_cases).values(**v))


def open_case_for_attempt(conn: Connection, attempt_id: str) -> Optional[Dict[str, Any]]:
    q = select(s.reconciliation_cases).where(and_(s.reconciliation_cases.c.attempt_id == attempt_id, s.reconciliation_cases.c.state == "open"))
    return _row(conn.execute(q).first())


def update_case(conn: Connection, case_id: str, **values) -> None:
    v = dict(values)
    if "resolved_at" in v:
        v["resolved_at"] = _n(v["resolved_at"])
    conn.execute(update(s.reconciliation_cases).where(s.reconciliation_cases.c.id == case_id).values(**v))


def list_cases(conn: Connection, watch_id: Optional[str] = None) -> List[Dict[str, Any]]:
    q = select(s.reconciliation_cases).order_by(s.reconciliation_cases.c.opened_at.asc())
    if watch_id:
        q = q.where(s.reconciliation_cases.c.watch_id == watch_id)
    return _rows(conn.execute(q))


# ---------------------------------------------------------- idempotency
def get_idempotent(conn: Connection, key: str, user_id: str, route: str) -> Optional[Dict[str, Any]]:
    q = select(s.idempotency_keys).where(
        and_(s.idempotency_keys.c.key == key, s.idempotency_keys.c.user_id == user_id, s.idempotency_keys.c.route == route)
    )
    return _row(conn.execute(q).first())


def put_idempotent(conn: Connection, key: str, user_id: str, route: str, request_digest: str, status: int, body: Any, at: datetime) -> None:
    conn.execute(
        insert(s.idempotency_keys).values(
            key=key, user_id=user_id, route=route, request_digest=request_digest, response_status=status, response_body=body, created_at=_n(at)
        )
    )


# ------------------------------------------------------ merchant health
def get_merchant_health(conn: Connection, merchant_id: str) -> Dict[str, Any]:
    row = _row(conn.execute(select(s.merchant_health).where(s.merchant_health.c.merchant_id == merchant_id)).first())
    if row is None:
        conn.execute(insert(s.merchant_health).values(merchant_id=merchant_id, consecutive_errors=0))
        row = {"merchant_id": merchant_id, "consecutive_errors": 0, "backoff_until": None, "last_call_at": None, "last_error": None}
    return row


def set_merchant_health(conn: Connection, merchant_id: str, **values) -> None:
    v = dict(values)
    for k in ("backoff_until", "last_call_at"):
        if k in v:
            v[k] = _n(v[k])
    conn.execute(update(s.merchant_health).where(s.merchant_health.c.merchant_id == merchant_id).values(**v))


# ---------------------------------------------------------- demo faults
def add_fault(conn: Connection, target: str, fault: str, count: int, at: datetime) -> str:
    fid = new_id("fault")
    conn.execute(insert(s.demo_faults).values(id=fid, target=target, fault=fault, remaining=count, created_at=_n(at)))
    return fid


def consume_fault(conn: Connection, target: str, fault: str) -> bool:
    row = conn.execute(
        select(s.demo_faults).where(and_(s.demo_faults.c.target == target, s.demo_faults.c.fault == fault, s.demo_faults.c.remaining > 0))
    ).first()
    if not row:
        return False
    conn.execute(update(s.demo_faults).where(s.demo_faults.c.id == row._mapping["id"]).values(remaining=row._mapping["remaining"] - 1))
    return True


def list_faults(conn: Connection) -> List[Dict[str, Any]]:
    return _rows(conn.execute(select(s.demo_faults).where(s.demo_faults.c.remaining > 0)))


def clear_faults(conn: Connection) -> None:
    conn.execute(delete(s.demo_faults))


# -------------------------------------------------------------- metrics
def bump_metric(conn: Connection, name: str, by: int = 1) -> None:
    res = conn.execute(update(s.metrics).where(s.metrics.c.name == name).values(value=s.metrics.c.value + by))
    if res.rowcount == 0:
        conn.execute(insert(s.metrics).values(name=name, value=by))


def all_metrics(conn: Connection) -> Dict[str, int]:
    return {r._mapping["name"]: r._mapping["value"] for r in conn.execute(select(s.metrics))}


def count_where(conn: Connection, table, *conds) -> int:
    q = select(func.count()).select_from(table)
    if conds:
        q = q.where(and_(*conds))
    return int(conn.execute(q).scalar() or 0)
