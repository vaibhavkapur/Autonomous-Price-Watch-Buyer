"""Relational schema (SQLAlchemy Core).

Concurrency guarantees are enforced here, not in application code alone:

* ``watches.version`` + ``watches.lease_token`` implement optimistic
  concurrency and a fencing token for workers.
* ``uq_purchase_attempts_active_claim`` is a partial unique index that allows
  at most one *active* purchase claim per watch (claimed → submitting →
  reconciliation_required → purchased). A second claim fails at the database.
"""
from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)

metadata = MetaData()

ACTIVE_CLAIM_STATES = ("claimed", "submitting", "reconciliation_required", "purchased")

users = Table(
    "users",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("name", String(200), nullable=False),
    Column("token_sha256", String(64), nullable=False, unique=True),
)

destinations = Table(
    "destinations",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("user_id", String(64), nullable=False, index=True),
    Column("label", String(200), nullable=False),
    Column("address", JSON, nullable=False),
)

agents = Table(
    "agents",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("name", String(200), nullable=False),
    Column("key_id", String(120), nullable=False),
    Column("public_jwk", JSON, nullable=False),
)

watches = Table(
    "watches",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("user_id", String(64), nullable=False, index=True),
    Column("agent_id", String(64), nullable=False),
    Column("version", Integer, nullable=False, default=1),
    Column("status", String(40), nullable=False, index=True),
    Column("original_request", Text, nullable=True),
    Column("product_constraints", JSON, nullable=False),
    Column("merchant_sku_mapping", JSON, nullable=False),
    Column("price_operator", String(8), nullable=False),
    Column("threshold_minor", Integer, nullable=False),
    Column("currency", String(3), nullable=False),
    Column("quantity", Integer, nullable=False, default=1),
    Column("destination_id", String(64), nullable=True),
    Column("allowed_merchants", JSON, nullable=False),
    Column("max_purchases", Integer, nullable=False, default=1),
    Column("purchases_completed", Integer, nullable=False, default=0),
    Column("authorization_profile", String(16), nullable=False, default="ap2"),
    Column("expires_at", DateTime, nullable=False),
    Column("timezone", String(64), nullable=False, default="UTC"),
    Column("poll_interval_seconds", Integer, nullable=False, default=60),
    Column("schedule_version", Integer, nullable=False, default=1),
    Column("next_check_at", DateTime, nullable=True, index=True),
    Column("lease_token", String(64), nullable=True),
    Column("lease_owner", String(120), nullable=True),
    Column("lease_expires_at", DateTime, nullable=True),
    Column("consecutive_errors", Integer, nullable=False, default=0),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
    Column("cancelled_at", DateTime, nullable=True),
    Column("cancel_requested", Boolean, nullable=False, default=False),
)

authorizations = Table(
    "authorizations",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("watch_id", String(64), nullable=False, index=True),
    Column("watch_version", Integer, nullable=False),
    Column("profile", String(16), nullable=False),
    Column("profile_version", String(40), nullable=False),
    Column("artifact_reference", String(64), nullable=False),
    Column("agent_key_id", String(120), nullable=False),
    Column("consent_reference", String(64), nullable=False),
    Column("status", String(24), nullable=False),  # active | consumed | revoked | expired
    Column("presentation_state", String(24), nullable=False, default="idle"),  # idle | pending | rejected | accepted
    Column("pending_attempt_id", String(64), nullable=True),
    Column("expires_at", DateTime, nullable=False),
    Column("summary", JSON, nullable=False),  # human-reviewable normalised constraints
    Column("created_at", DateTime, nullable=False),
)

protocol_artifacts = Table(
    "protocol_artifacts",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("kind", String(64), nullable=False),
    Column("watch_id", String(64), nullable=True, index=True),
    Column("authorization_id", String(64), nullable=True),
    Column("attempt_id", String(64), nullable=True, index=True),
    Column("digest", String(64), nullable=False),
    Column("ciphertext", Text, nullable=False),  # Fernet-encrypted native artifact
    Column("created_at", DateTime, nullable=False),
)

offer_observations = Table(
    "offer_observations",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("watch_id", String(64), nullable=False, index=True),
    Column("evaluation_run_id", String(64), nullable=True, index=True),
    Column("merchant_id", String(64), nullable=False),
    Column("native_offer_id", String(120), nullable=True),
    Column("sku", String(120), nullable=True),
    Column("variant", JSON, nullable=True),
    Column("quantity_available", Integer, nullable=True),
    Column("price_components", JSON, nullable=False),
    Column("total_minor", Integer, nullable=True),
    Column("currency", String(3), nullable=True),
    Column("observed_at", DateTime, nullable=False),
    Column("valid_until", DateTime, nullable=True),
    Column("delivery_estimate", String(120), nullable=True),
    Column("source_reference", String(200), nullable=False),
    Column("eligibility", String(32), nullable=False),  # eligible | rejected | incomplete | stale | error
    Column("reasons", JSON, nullable=False),
)

evaluation_runs = Table(
    "evaluation_runs",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("watch_id", String(64), nullable=False, index=True),
    Column("watch_version", Integer, nullable=False),
    Column("lease_token", String(64), nullable=False),
    Column("worker_id", String(120), nullable=False),
    Column("started_at", DateTime, nullable=False),
    Column("finished_at", DateTime, nullable=True),
    Column("selected_offer_id", String(64), nullable=True),
    Column("outcome", String(40), nullable=True),
    Column("details", JSON, nullable=True),
)

purchase_attempts = Table(
    "purchase_attempts",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("watch_id", String(64), nullable=False, index=True),
    Column("watch_version", Integer, nullable=False),
    Column("authorization_id", String(64), nullable=False),
    Column("evaluation_run_id", String(64), nullable=True),
    Column("merchant_id", String(64), nullable=False),
    Column("checkout_id", String(120), nullable=False),
    Column("checkout_digest", String(64), nullable=False),
    Column("final_total_minor", Integer, nullable=False),
    Column("currency", String(3), nullable=False),
    Column("idempotency_key", String(64), nullable=False, unique=True),
    Column("claim_state", String(32), nullable=False, index=True),
    # claimed | submitting | reconciliation_required | purchased | aborted | rejected | resolved_not_purchased
    Column("order_state", String(32), nullable=False, default="none"),
    Column("payment_state", String(32), nullable=False, default="none"),
    Column("external_references", JSON, nullable=False, default=dict),
    Column("claimed_at", DateTime, nullable=False),
    Column("submitted_at", DateTime, nullable=True),
    Column("resolved_at", DateTime, nullable=True),
    Column("last_error", Text, nullable=True),
    Index(
        "uq_purchase_attempts_active_claim",
        "watch_id",
        unique=True,
        postgresql_where=text("claim_state IN ('claimed','submitting','reconciliation_required','purchased')"),
        sqlite_where=text("claim_state IN ('claimed','submitting','reconciliation_required','purchased')"),
    ),
)

outbound_events = Table(
    "outbound_events",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("attempt_id", String(64), nullable=False, index=True),
    Column("kind", String(40), nullable=False),  # ucp.complete_checkout
    Column("idempotency_key", String(64), nullable=False),
    Column("target", String(200), nullable=False),
    Column("payload_digest", String(64), nullable=False),
    Column("state", String(24), nullable=False),  # prepared | sent_unknown | acknowledged | failed
    Column("created_at", DateTime, nullable=False),
    Column("sent_at", DateTime, nullable=True),
    Column("acknowledged_at", DateTime, nullable=True),
    Column("response_digest", String(64), nullable=True),
)

watch_events = Table(
    "watch_events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("watch_id", String(64), nullable=False, index=True),
    Column("at", DateTime, nullable=False),
    Column("type", String(64), nullable=False),
    Column("actor", String(120), nullable=False),
    Column("watch_version", Integer, nullable=True),
    Column("details", JSON, nullable=False),
)

notifications = Table(
    "notifications",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("watch_id", String(64), nullable=False, index=True),
    Column("user_id", String(64), nullable=False),
    Column("dedupe_key", String(120), nullable=False, unique=True),
    Column("channel", String(40), nullable=False),
    Column("subject", String(200), nullable=False),
    Column("body", Text, nullable=False),
    Column("state", String(24), nullable=False),  # queued | delivered | suppressed
    Column("created_at", DateTime, nullable=False),
    Column("delivered_at", DateTime, nullable=True),
)

reconciliation_cases = Table(
    "reconciliation_cases",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("attempt_id", String(64), nullable=False, index=True),
    Column("watch_id", String(64), nullable=False, index=True),
    Column("state", String(24), nullable=False),  # open | resolved
    Column("summary", Text, nullable=False),
    Column("evidence", JSON, nullable=False),
    Column("checks", Integer, nullable=False, default=0),
    Column("opened_at", DateTime, nullable=False),
    Column("resolved_at", DateTime, nullable=True),
    Column("resolution", String(40), nullable=True),
)

idempotency_keys = Table(
    "idempotency_keys",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("key", String(120), nullable=False),
    Column("user_id", String(64), nullable=False),
    Column("route", String(120), nullable=False),
    Column("request_digest", String(64), nullable=False),
    Column("response_status", Integer, nullable=False),
    Column("response_body", JSON, nullable=False),
    Column("created_at", DateTime, nullable=False),
    UniqueConstraint("key", "user_id", "route", name="uq_idempotency_scope"),
)

merchant_health = Table(
    "merchant_health",
    metadata,
    Column("merchant_id", String(64), primary_key=True),
    Column("consecutive_errors", Integer, nullable=False, default=0),
    Column("backoff_until", DateTime, nullable=True),
    Column("last_call_at", DateTime, nullable=True),
    Column("last_error", Text, nullable=True),
)

demo_faults = Table(
    "demo_faults",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("target", String(64), nullable=False),  # merchant id or 'adapter'
    Column("fault", String(64), nullable=False),
    Column("remaining", Integer, nullable=False),
    Column("created_at", DateTime, nullable=False),
)

metrics = Table(
    "metrics",
    metadata,
    Column("name", String(80), primary_key=True),
    Column("value", Integer, nullable=False, default=0),
)
