# Autonomous Price-Watch Buyer — Development Plan

## 1. Project summary

Build an **agent that watches an exact product and makes one purchase when the user's authorized conditions are satisfied**.

Example instruction:

> “Buy this exact keyboard if its delivered price drops below $100 before Sunday. Use only these two merchants and buy it once.”

The system should:

- resolve the request into precise product and price constraints
- obtain bounded purchase authorization
- monitor merchant offers while the user is absent
- obtain a fresh final checkout before purchasing
- verify identity, total cost, expiry, and authorization
- execute one logical purchase
- recover from timeouts without buying twice
- return an order receipt and a decision history

The core product is **a durable price watcher with delegated purchase authority and a reliable single-purchase workflow**.

Planning baseline: **25 September 2026**. This plan describes a project to build; it does not schedule a live watch or authorize an actual purchase.

---

## 2. Why this project is compelling

The user delegates a future decision instead of approving a checkout while present. That makes time, changing prices, stale information, and execution races central to the design.

The project demonstrates:

- translating natural language into reviewable constraints
- scheduled work that survives restarts
- authorization that remains bounded while the user is absent
- exact product matching
- fresh checkout validation
- single-purchase enforcement across competing offers
- recovery when the external outcome is unknown

Compared with the procurement project, the focus is less on supplier collaboration and more on durable autonomy over time.

---

## 3. Protocol responsibilities

### UCP — merchant interaction

Use a supported UCP profile for merchant discovery and checkout. Obtain catalog/offer data through a capability supported by the pinned release, or a clearly labeled application feed. Revalidate the final checkout before completion. [UCP specification](https://ucp.dev/specification/overview/)

### AP2 — primary authorization profile

Implement an autonomous purchase using the checkout/payment authorization model defined by the selected AP2 release. Match the UCP payment integration to the AP2 version rather than assuming all releases interoperate. [AP2 specification](https://ap2-protocol.org/ap2/specification/)

### Verifiable Intent — second authorization profile

Add a separate mode demonstrating its delegation and disclosure rules for the same shopping scenario. Treat it as a versioned profile with separate test fixtures. [Verifiable Intent](https://github.com/agent-intent/verifiable-intent)

### Optional TAP

Add TAP request verification at the merchant boundary after the core watch works. It complements purchase authorization and does not replace it. [Visa TAP](https://github.com/visa/trusted-agent-protocol)

Scheduling, price comparison, product matching, and application cancellation are product responsibilities, not automatically supplied by these protocols.

---

## 4. MVP scope

### Build first

- one user and one agent
- one exact product variant
- two controlled merchant fixtures
- one currency: USD
- a strict delivered-price threshold
- a deadline and merchant allowlist
- one permitted purchase
- persistent polling with configurable cadence
- UCP checkout and one compatible AP2 authorization path
- real signing/verification with simulated payment execution

### Complete portfolio release

- second authorization mode using Verifiable Intent
- actual protocol exchanges with both merchant fixtures
- recovery and concurrency tests
- one provider sandbox or testnet execution path if supported

### Defer

- general web scraping
- substitution with similar products
- buying multiple units over time
- auctions and bidding
- recurring replenishment
- broad coupon search
- production merchant enrollment

Keep the initial purchase rule narrow enough that a verifier can explain every decision.

---

## 5. Recommended technology stack

- **Frontend:** Next.js and TypeScript
- **Application API:** FastAPI
- **Persistence:** PostgreSQL
- **Scheduler:** database-backed schedule and durable worker
- **Queue:** Redis/Celery if needed; the database remains authoritative
- **Authorization:** pinned protocol libraries and maintained JOSE tooling
- **Merchant fixtures:** lightweight local HTTP services
- **Testing:** pytest with injected clock and deterministic event schedules
- **Local environment:** Docker Compose

An LLM helps create and explain the watch. Polling and purchase eligibility should run deterministically without requiring an LLM call every minute.

---

## 6. High-level architecture

```text
User → Watch creation UI → Constraint review → Authorization signer
                                  ↓
                             Watch store
                                  ↓
                           Durable scheduler
                                  ↓
Offer collectors → Candidate evaluator → Fresh checkout builder
                                             ↓
                                    Authorization verifier
                                             ↓
                                    Purchase coordinator
                                             ↓
                                      UCP merchant
                                             ↓
                                  Order/payment reconciliation
```

The scheduler can request evaluation. Only the purchase coordinator can claim a watch and initiate execution.

---

## 7. Watch creation and consent

Convert conversational input into explicit fields:

- canonical product and variant
- merchant-specific SKU mappings
- quantity
- delivered-price threshold and comparison operator
- currency
- delivery destination
- merchant allowlist
- deadline with time zone
- maximum successful purchases

Show the normalized rule before the user signs. For example, “below $100” is a strict comparison. With USD cents, the largest qualifying total is $99.99.

Make unknowns explicit. A missing shipping address can prevent accurate tax/shipping calculation; do not activate a purchase-ready watch until required information is available.

---

## 8. Application watch model

Example application object, not a protocol mandate:

```json
{
  "watch_id": "watch_001",
  "product_id": "keyboard_k1_black_us",
  "merchant_skus": {
    "merchant_a": "K1-BLK-US",
    "merchant_b": "KB-K1-US-B"
  },
  "quantity": 1,
  "currency": "USD",
  "price_rule": {
    "operator": "lt",
    "delivered_total_minor": "10000"
  },
  "allowed_merchants": ["merchant_a", "merchant_b"],
  "destination_id": "address_1",
  "expires_at": "2026-09-27T18:00:00+05:30",
  "max_purchases": 1,
  "authorization_profile": "ap2"
}
```

Where a protocol supports only an inclusive maximum, translate this rule to the exact allowed integer ceiling and display that translation during review. Keep the original user wording as context, not as the executable rule.

---

## 9. Product identity and offer normalization

Resolve identity using merchant catalog identifiers and an explicit mapping established during watch creation. A model's similarity judgment is insufficient to authorize a substitute.

For each offer, store:

- merchant and native offer reference
- product SKU and selected variant
- quantity available
- item subtotal
- shipping, tax, and mandatory fees
- total and currency
- retrieval time and validity period
- delivery estimate
- source evidence reference

Descriptions such as “similar keyboard” cannot satisfy an exact-SKU watch. Reject a different layout, color, bundle, condition, or quantity unless the user explicitly authorized it.

If product fields are descriptive rather than machine-enforced in the selected authorization profile, apply deterministic application checks to the merchant checkout. [VI constraint scope](https://github.com/agent-intent/verifiable-intent)

---

## 10. Delivered-price calculation

Use exact integer arithmetic:

```text
delivered total = item subtotal + tax + shipping + mandatory fees - valid discounts
```

Only apply a discount if the final checkout confirms it for this user and order. Store each component with its source and calculation timestamp.

Example fixtures:

- item $89 + shipping $12 + tax $4 = $105 → reject
- item $89 + shipping $5 + tax $4 = $98 → eligible
- final total exactly $100 → reject for a strict “below $100” rule

An unknown mandatory charge prevents autonomous execution. A quoted item price is not sufficient proof that the delivered purchase fits the budget.

---

## 11. Scheduler and worker design

Store `next_check_at` and a persistent schedule version. Workers claim due watches using a lease with a fencing token or equivalent database concurrency control.

Recommended behavior:

- bounded polling interval with jitter
- backoff after merchant errors
- per-merchant rate limits
- no catch-up storm after downtime
- explicit expiry checks using a server clock
- one active evaluation lease per watch
- reschedule only if the watch remains active

Queue messages are hints to re-read durable state. Duplicate messages must be harmless.

A stale worker cannot overwrite a newer watch version or begin a purchase after another worker has claimed it.

---

## 12. End-to-end autonomous purchase flow

1. Scheduler claims an active watch.
2. Collector retrieves offers from permitted merchants.
3. Evaluator discards stale, incomplete, or wrong-product offers.
4. Eligible offers are ranked by delivered total and a deterministic tie-breaker.
5. Workflow creates a fresh checkout with the selected merchant.
6. Evaluator checks every final checkout field against the watch.
7. Authorization adapter prepares and verifies transaction-specific evidence.
8. Coordinator atomically claims the watch for this checkout.
9. Execution attempt and outbound event are persisted.
10. Worker rechecks expiry/cancellation before external submission.
11. Merchant completion executes under a stable operation identity.
12. Reconciliation resolves order and payment status.
13. Successful watch becomes purchased and stops scheduling.
14. User receives the result through the project's configured notification channel.

The notification service is part of the proposed application; implementing this plan does not imply sending real messages during development.

---

## 13. Authorization lifecycle

The user authorizes the watch's bounds. The agent may act only within them and only while authority remains valid.

Keep the grant, watch version, agent key, and execution claim linked. Changes to product, maximum price, merchant scope, deadline, or destination require a new consent review and appropriate new authorization.

For AP2 autonomous execution, follow the selected release's evidence and mandate-use rules. Once a purchase authorization has been presented, an uncertain response is not permission to authorize a different checkout. Resolve the first attempt and obtain any required rejection evidence before proceeding. [AP2 autonomous flow](https://ap2-protocol.org/ap2/specification/)

For VI, use its profile-specific verification and disclosure rules. Do not relabel AP2 artifacts as VI artifacts.

---

## 14. Data model

### `watches`

- `id`, `user_id`, `agent_id`, `version`
- `product_constraints`, `merchant_sku_mapping`
- `price_operator`, `threshold_minor`, `currency`
- `quantity`, `destination_id`, `allowed_merchants`
- `status`, `next_check_at`, `expires_at`

### `authorizations`

- `id`, `watch_id`, `watch_version`, `profile`, `profile_version`
- `artifact_reference`, `agent_key_id`, `consent_reference`
- `status`, `expires_at`

### `offer_observations`

- `id`, `watch_id`, `merchant_id`, `native_offer_id`
- `sku`, `price_components`, `total_minor`, `currency`
- `observed_at`, `valid_until`, `eligibility`, `reasons`

### `evaluation_runs`

- `id`, `watch_id`, `watch_version`, `lease_token`
- `started_at`, `finished_at`, `selected_offer_id`, `outcome`

### `purchase_attempts`

- `id`, `watch_id`, `authorization_id`, `checkout_id`
- `checkout_digest`, `idempotency_key`, `claim_state`
- `order_state`, `payment_state`, `external_references`
- `submitted_at`, `resolved_at`

### Supporting records

- `watch_events`: immutable decision and lifecycle history
- `notifications`: delivery state and deduplication identity
- `protocol_artifacts`: protected native evidence
- `reconciliation_cases`: uncertain or inconsistent external outcomes

Use database uniqueness and conditional updates to enforce one active purchase claim per watch.

---

## 15. API design

These are project APIs. Native merchant and authorization exchanges use their pinned specifications.

```http
POST /v1/watches
GET  /v1/watches
GET  /v1/watches/{id}
POST /v1/watches/{id}/authorization-proposal
POST /v1/watches/{id}/activate
POST /v1/watches/{id}/pause
POST /v1/watches/{id}/resume
POST /v1/watches/{id}/cancel
GET  /v1/watches/{id}/observations
GET  /v1/watches/{id}/timeline
GET  /v1/watches/{id}/purchase
```

Local-only test controls:

```http
POST /demo/merchants/{id}/price-scenario
POST /demo/watches/{id}/evaluate-now
POST /demo/faults
```

Disable demo controls outside the development profile. Mutation requests require watch ownership, expected version, and idempotency handling.

---

## 16. State machines

```text
draft → awaiting_authorization → watching
watching → paused → watching
watching → candidate_found → checkout_validating → purchase_claimed
checkout_validating → watching  [offer no longer qualifies]
purchase_claimed → submitting → purchased
submitting → reconciliation_required → purchased | resolved_not_purchased
watching | paused → cancelled | expired
```

Track authorization and payment separately from watch status.

`resolved_not_purchased` does not automatically reactivate authority. Follow the profile's retry/rejection rules and request new consent where required.

A user cancellation after external submission stops future activity but may not cancel the existing order. Display that distinction explicitly.

---

## 17. Single-purchase and deadline guarantees

Two merchants may become eligible simultaneously. Both can produce observations, but only one checkout can acquire the watch's purchase claim.

Persist the claim before submission. Use the merchant's supported idempotency behavior and native references for external deduplication. Database locking alone cannot guarantee uniqueness inside an external merchant system.

Check expiry at evaluation, authorization, and submission. Creating a queue job before the deadline does not authorize execution after it.

Keep uncertain attempts claimed until reconciled. Never release a claim solely because a network call timed out.

The guarantee to demonstrate is one logical purchase in the tested merchant/profile configuration, with clear assumptions about merchant idempotency and result lookup.

---

## 18. Failure modes

### Shipping or tax changes

Reject the new total if it exceeds the rule. Continue watching only when no purchase has been committed and authorization rules permit it.

### Stock disappears

Record the failed candidate and resume evaluation under the same conditions if safe.

### Response lost after purchase

Query the existing checkout/order. Do not buy from the second merchant.

### Scheduler restarts

Recover schedules from the database and continue with bounded polling.

### Watch cancelled during evaluation

The coordinator re-reads the current version and cancellation state before claiming execution.

### Authorization expires during checkout preparation

Stop before submission and request renewed authority through the user interface.

### Payment succeeds but order is unclear

Open a reconciliation case, preserve financial evidence, and report uncertainty without representing it as failure or success prematurely.

---

## 19. Security and privacy

- Treat merchant descriptions as untrusted content.
- Verify product mappings from configured merchant data.
- Keep signing keys outside the model and ordinary application logs.
- Restrict merchant discovery and redirects to intended destinations.
- Encrypt sensitive authorization artifacts and address references.
- Authenticate watch-management and approval actions.
- Log reasons and evidence references without exposing reusable credentials.

Application cancellation uses an online state check in this design. Signed offline artifacts do not automatically acquire an externally visible revocation mechanism.

---

## 20. User interface and metrics

Build four views:

1. **Create watch:** exact product, threshold, merchant scope, deadline.
2. **Authorization review:** the actual conditions being delegated.
3. **Watch detail:** recent offers, total-price breakdowns, rejected candidates.
4. **Purchase detail:** final checkout, authorization result, order/payment status, receipt.

Show why a $89 advertised product was rejected when its delivered cost reached $105.

Track observation freshness, polling success, qualifying offers, purchase attempts, duplicate claims prevented, and unresolved executions. Use a simulated clock for the accelerated demo and label it visibly.

---

## 21. Phased delivery plan

### Phase 1 — deterministic watcher

Build watch CRUD, SKU mapping, price fixtures, delivered-total evaluation, and durable scheduling.

Success: the strict threshold and expiry boundaries behave correctly across restarts.

### Phase 2 — UCP checkout and authorization

Confirm a compatible UCP/AP2 profile pair, implement the actual exchanges, and bind the final checkout to the user's constraints.

Success: a valid candidate executes and changed final terms are rejected.

### Phase 3 — recovery and second profile

Add atomic claims, lost-response recovery, cancellation races, and the VI mode.

Success: parallel eligibility events and duplicate jobs cannot create a second purchase.

### Phase 4 — portfolio polish

Add the trace viewer, notifications, testnet/sandbox path where supported, and recorded demos.

Success: an observer can explain the purchase decision and verify its evidence from the UI.

---

## 22. Roadmap and repository structure

Planning estimate: **three to four focused weeks**, with protocol compatibility checked before implementation.

- Week 1: watch domain, scheduler, price rules, fixture merchant.
- Week 2: UCP checkout, primary authorization profile, execution coordinator.
- Week 3: recovery, second authorization profile, concurrency tests.
- Week 4 if needed: sandbox path, UI, documentation, demo capture.

```text
autonomous-price-watch/
  apps/{web,api,worker,merchant-a,merchant-b}/
  packages/
    watch-domain/
    product-identity/
    offer-evaluation/
    ucp-adapter/
    authorization-profiles/
    purchase-coordinator/
    notifications/
  fixtures/{catalogs,price-scenarios,authorizations}/
  tests/{rules,scheduling,protocol,concurrency,recovery}/
  migrations/
  docs/
  docker-compose.yml
```

---

## 23. Testing and demo scenarios

### Required tests

- $99.99 passes and $100.00 fails a strict $100 threshold
- missing shipping or tax prevents execution
- wrong product variant is rejected
- merchant outside the allowlist is rejected
- duplicate scheduler messages are harmless
- simultaneous qualifying merchants yield one execution claim
- stale worker lease cannot submit
- cancellation wins before submission when serialized first
- expired authorization blocks submission
- payment/order timeout preserves recovery state

### Demo A — deceptive item price

An item falls to $89, but delivered total is $105. Show rejection.

### Demo B — valid drop

Delivered total becomes $98. The user is absent; the purchase succeeds within delegated conditions.

### Demo C — concurrent opportunity

Both merchants qualify. Show one winner and one order.

### Demo D — recovery

Drop the completion response and restart the worker. Recover the existing order without buying again.

---

## 24. Definition of done, references, and next steps

The project is complete when it performs a bounded autonomous purchase through real protocol exchanges, enforces exact product/price/time constraints, and passes duplicate, restart, and ambiguous-outcome tests.

Include a protocol manifest, state diagrams, setup instructions, reproducible price scenarios, test report, redacted evidence bundle, and a short demo video.

Primary references:

- [UCP specification](https://ucp.dev/specification/overview/)
- [AP2 specification](https://ap2-protocol.org/ap2/specification/)
- [Verifiable Intent](https://github.com/agent-intent/verifiable-intent)
- [Optional TAP integration](https://github.com/visa/trusted-agent-protocol)

Portfolio wording after completion:

> Built an autonomous price-watch buyer with UCP checkout and verifiable purchase authorization, enforcing exact product, delivered-price, merchant, and expiry constraints while recovering safely from concurrent triggers and uncertain order outcomes.

Start with one watch, a controlled clock, two price fixtures, and a database-enforced purchase claim. Then connect real protocol adapters to the same tested workflow.

**One-sentence summary:** An agent that waits for the right offer and buys once, within the user's explicit authority, even when the user is absent.
