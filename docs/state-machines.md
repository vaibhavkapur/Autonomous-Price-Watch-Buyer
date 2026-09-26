# State machines

## Watch status (`watches.status`)

Enforced by `watch_domain.states.transition`; every transition is a versioned,
lease-fenced database update and appends a `watch_events` row.

```mermaid
stateDiagram-v2
    [*] --> draft
    draft --> awaiting_authorization: POST authorization-proposal
    awaiting_authorization --> watching: POST activate (consent signed)
    watching --> paused: user pause / no active authorization / destination missing
    paused --> watching: user resume (active authorization required)
    watching --> candidate_found: eligible offer ranked
    candidate_found --> checkout_validating: fresh UCP checkout created & signed
    checkout_validating --> watching: offer no longer qualifies at checkout
    checkout_validating --> purchase_claimed: atomic claim (unique active attempt)
    purchase_claimed --> submitting: expiry/cancel/authority rechecked, outbound event persisted
    purchase_claimed --> watching: aborted before submission (nothing sent)
    submitting --> purchased: merchant returns completed + order
    submitting --> watching: verifier rejection with receipt (AP2) → may retry
    submitting --> reconciliation_required: timeout / 5xx / 409 / complete_in_progress
    reconciliation_required --> purchased: Get Checkout shows completed
    reconciliation_required --> resolved_not_purchased: Get Checkout shows canceled / no order
    watching --> cancelled
    paused --> cancelled
    candidate_found --> cancelled
    checkout_validating --> cancelled
    purchase_claimed --> cancelled: cancel observed before submission
    watching --> expired
    paused --> expired
    candidate_found --> expired
    checkout_validating --> expired
    purchase_claimed --> expired: deadline observed before submission
    purchased --> [*]
    resolved_not_purchased --> [*]
    cancelled --> [*]
    expired --> [*]
```

Notes

* Only `watching` is schedulable. `reconciliation_required` is worked by the reconciler, never by the poller.
* A user cancellation while `purchase_claimed | submitting | reconciliation_required` only sets `cancel_requested`; the timeline records that an order already submitted may not be cancellable.
* `resolved_not_purchased` does not reactivate authority; the authorization is marked `consumed` and new consent is required.

## Purchase attempt (`purchase_attempts.claim_state`)

```mermaid
stateDiagram-v2
    [*] --> claimed: INSERT under partial unique index (watch_id) WHERE active
    claimed --> aborted: cancel / deadline / authority expired before submission (nothing sent)
    claimed --> rejected: payment verifier Error receipt
    claimed --> submitting: outbound event sent_unknown
    submitting --> purchased: completed + order
    submitting --> rejected: merchant Error receipt / 4xx (not processed)
    submitting --> reconciliation_required: unknown outcome
    reconciliation_required --> purchased: order found via Get Checkout / identical resubmission
    reconciliation_required --> resolved_not_purchased: merchant shows no order
```

Active states (`claimed, submitting, reconciliation_required, purchased`) are the
ones covered by `uq_purchase_attempts_active_claim`; a second row for the same
watch in any active state is refused by the database.

## Authorization (`authorizations.status` × `presentation_state`)

```text
status:             active ──► consumed | revoked | expired
presentation_state: idle ──► pending ──► accepted
                                  └────► rejected ──► pending (AP2 only; VI is single-use)
```

* `pending` is set inside the claim transaction and blocks any further presentation until a receipt (success or rejection) is recorded — the AP2 "no subsequent open mandate presentation without a rejection receipt" rule.
* Expiry of authority is checked at evaluation, at claim, and again immediately before external submission, always with the server clock.

## Outbound event (`outbound_events.state`)

```text
prepared ──► sent_unknown ──► acknowledged
                        └───► (stays sent_unknown until reconciliation resolves the attempt)
prepared ──► failed (4xx, definitively not processed)
```

The event (idempotency key, target, payload digest) is written **before** the
request leaves the process, so a restart can tell "never sent" from "sent, outcome unknown".

## UCP checkout session (merchant side)

```text
incomplete ⇄ requires_escalation
incomplete ──► ready_for_complete ──► completed
any non-terminal ──► canceled (cancel / expires_at)
```

Complete Checkout is accepted only from `ready_for_complete`, requires an
`Idempotency-Key`, replays the stored response for the same key + payload, and
returns `409` for the same key with a different payload or for a checkout already
completed under a different key.
