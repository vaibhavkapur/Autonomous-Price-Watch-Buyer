# Database and Evidence

[Documentation home](index.md)

## Persistence

SQLAlchemy tables in `packages/persistence/schema.py` store watches, observations, authorizations, purchase attempts, outbound events, schedules/leases, reconciliation cases, artifacts, and audit data. SQLite supports the local demo; PostgreSQL supports the Compose topology.

`migrations/001_initial.sql` is generated from that schema. Compose runs `scripts/migrate.py` before API, worker, and merchant startup. `make migration` regenerates the SQL file for intentional schema changes; it is not required to start the existing demo.

## Single-purchase protection

The partial unique index `uq_purchase_attempts_active_claim` covers active execution states, including purchased attempts. Version checks and leases prevent stale workers from acting; the index prevents a second active claim even when application-level concurrency checks race.

Outbound events record the target, idempotency key, and request digest before submission. An unknown response retains the attempt for reconciliation. Database state therefore must survive while a merchant may have accepted the purchase.

## Evidence retention

The artifact vault stores authorization and receipt evidence with digests and protected content. API evidence views redact reusable credentials. The [Evidence Bundle](evidence-bundle.md) explains exported AP2 and VI examples and what each artifact establishes.

Keep database, fixture keys, and artifact encryption material together for recovery. Compose uses `pgdata` and `devkeys` volumes. Merchant fixture order state is process-local, so preserving those volumes alone does not provide durable recovery after every merchant container is recreated.
