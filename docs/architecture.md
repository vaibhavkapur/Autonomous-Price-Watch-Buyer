# Architecture

[Documentation home](index.md)

## Overview

The watcher separates product discovery from purchase authority. A cheap fixture feed supplies candidate prices; a signed UCP checkout provides the authoritative product, delivered total, and merchant identity immediately before execution.

```text
User → Draft watch → Reviewed constraints → AP2 or VI authorization
                             ↓
                 Database schedule → Leased worker
                             ↓
                 Merchant feed → Candidate evaluation
                             ↓
                 Signed UCP checkout → Revalidation
                             ↓
                 Atomic purchase claim → Complete checkout
                             ↓
                  Order + evidence / reconciliation
```

## Components and technology

- `apps/api`: FastAPI watch and demo routes; `apps/web`: Next.js UI.
- `apps/worker`: scheduled evaluation and reconciliation; `apps/dev_server.py` embeds API, fixtures, and a worker for local use.
- `packages/watch_domain`: parsing, normalized rules, watch lifecycle, and consent.
- `packages/product_identity` and `packages/offer_evaluation`: exact SKU/variant and delivered-price validation.
- `packages/scheduler`: leases, polling cadence, merchant backoff, and deadlines.
- `packages/ucp_adapter` and `packages/merchant_fixture`: UCP client and owned merchants.
- `packages/authorization_profiles`: distinct AP2 and VI presentations plus fixture payment verification.
- `packages/purchase_coordinator` and `packages/persistence`: submission/recovery, SQLAlchemy data, and protected evidence.

## Execution guarantees and limits

A worker must hold a current lease and recheck the watch and authorization before external submission. A partial unique database index prevents a second active purchase claim for the same watch. Unknown completion preserves that claim until reconciliation establishes the outcome.

Product identity, currency, shipping, tax, expiry, and delivered total are checked against checkout data. A lower item price alone is not enough. Cancellation after submission cannot recall an accepted external purchase.

See [State Machines](state-machines.md) for watch, attempt, authorization, and outbound-event transitions. [Protocol Manifest](protocol-manifest.md) distinguishes standard structures from application-defined placement and the deferred TAP layer.
