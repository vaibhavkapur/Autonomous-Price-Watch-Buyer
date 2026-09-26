# Protocol manifest

Pinned protocol releases and how each is used. Anything not covered by a pinned
specification is labelled **application-defined** here and in code.

| Layer | Protocol / release | Where implemented | Pinned to |
|---|---|---|---|
| Merchant discovery + checkout | **UCP `2026-08-25`** — business profile at `/.well-known/ucp`, `dev.ucp.shopping.checkout` over REST, `UCP-Agent` header, `Idempotency-Key` on Complete Checkout, `dev.ucp.ap2_mandate_compatible_handlers` payment handler family | `packages/ucp_adapter` (platform), `packages/merchant_fixture` (business) | [ucp.dev/specification/overview](https://ucp.dev/specification/overview/), [checkout capability](https://ucp.dev/specification/checkout/) |
| Primary authorization | **AP2 v0.2** — autonomous (Human Not Present) mandates: open `mandate.checkout.open.1` / `mandate.payment.open.1` with `cnf`, closed `mandate.checkout.1` / `mandate.payment.1` bound to the merchant-signed `checkout_jwt`; constraints `checkout.allowed_merchants`, `checkout.line_items`, `payment.amount_range`, `payment.allowed_payees`, `payment.reference`, `payment.execution_date`; Checkout/Payment Receipts | `packages/authorization_profiles/ap2.py` | [AP2 specification](https://ap2-protocol.org/ap2/specification/), [checkout mandate](https://ap2-protocol.org/ap2/checkout_mandate/), [payment mandate](https://ap2-protocol.org/ap2/payment_mandate/), [agent authorization](https://ap2-protocol.org/ap2/agent_authorization/) |
| Second authorization | **Verifiable Intent v0.1-draft (2026-02-18)** — 3-layer autonomous mode: L1 `sd+jwt`, L2 `kb-sd-jwt+kb`, L3a/L3b `kb-sd-jwt`; constraints `mandate.checkout.allowed_merchants`, `mandate.checkout.line_items`, `mandate.payment.amount_range`, `mandate.payment.allowed_payees`, `mandate.payment.reference` | `packages/authorization_profiles/vi.py` | [spec/credential-format.md](https://github.com/agent-intent/verifiable-intent/blob/main/spec/credential-format.md), [spec/constraints.md](https://github.com/agent-intent/verifiable-intent/blob/main/spec/constraints.md) |
| Credential format | SD-JWT (RFC 9901) disclosures, `_sd` / `{"...": digest}`, `~` serialization, `sd_hash`; JWS ES256 via `joserfc` | `packages/authorization_profiles/sdjwt.py` | RFC 9901, RFC 7515/7518/7800 |
| Optional TAP | **Not implemented** (deferred per plan §3; would sit at the merchant boundary and complement, not replace, the above) | — | — |

## UCP details

* The platform pins `ucp.version == 2026-08-25` and refuses merchants advertising anything else (`version_unsupported`).
* Discovery requires `dev.ucp.shopping.checkout` at the pinned version and a non-empty `keys[]` (used to verify the merchant-signed checkout).
* Checkout lifecycle statuses used: `incomplete`, `requires_escalation`, `ready_for_complete`, `complete_in_progress`, `completed`, `canceled`. Only `ready_for_complete` checkouts are ever completed autonomously.
* `totals[]` types mapped to the delivered-price formula: `subtotal` + `fulfillment` + `tax` + `fee` − `discount` = `total`. A missing `tax`/`fulfillment`/`total` entry makes the checkout *incomplete* and blocks execution.
* Complete Checkout carries the payment instrument (handler `ap2_fixture_handler` or `vi_fixture_handler`) plus authorization evidence:
  * AP2: `ap2.checkout_mandate` (open + closed chain) — the shape shown in UCP's "Scenario C: Autonomous Agent (AP2)".
  * VI: `vi.{l1,l2,l3b}` — **application-defined placement**; VI is transport-agnostic and UCP has no registered VI extension.
* The merchant-signed checkout is returned as `ap2.checkout_jwt` (`typ: JWT`, ES256, `kid` in the business profile `keys[]`). Payload: `id, merchant{id,name,website}, line_items, totals, currency, status, expires_at, iat`. The adapter verifies signature and that the signed payload equals the response body before using it. **Field placement is application-defined**; AP2 only requires a merchant-signed JWT of the Checkout, and VI §6.3 requires a machine-readable merchant `id` in it.
* Lost-response rule (UCP Complete Checkout): never poll with Complete; use Get Checkout with bounded backoff; only if Get Checkout cannot establish the outcome (`ready_for_complete`), resubmit the *identical* request with the *same* `Idempotency-Key`. Implemented in `PurchaseCoordinator.reconcile`.
* **Application feed** `GET /app-feed/offers` is *not* a UCP capability. It is a clearly labelled fixture feed (`source: application_feed`) used for cheap polling. The UCP checkout is always the authoritative revalidation before any authorization is bound.

## AP2 details (v0.2)

* Delegation model: **Trusted Agent Provider**. The Trusted Surface is deterministic server code (`WatchService.activate`) holding the `agent_provider` key; the LLM/agent never sees it. Consent produces one *delegate SD-JWT* (`typ: dc+sd-jwt`, `vct: local.pricewatch.ap2.agent_mandate` — application-defined credential type) whose `delegate_payload` references two element disclosures: the open Checkout Mandate and the open Payment Mandate. Merchants, payees and acceptable items are nested element disclosures so each verifier only receives what it needs.
* `cnf.jwk` = the Shopping Agent's P-256 key. `exp` = the watch deadline (smallest value that completes the task).
* Closing: a key-binding JWT (`typ: kb+sd-jwt`, signed by the agent key) with `aud` (`merchant` / `credential-provider`), `nonce`, `iat`, `sd_hash` over the exact open presentation string it extends, and `delegate_payload` referencing the closed mandate disclosure. The closed Checkout Mandate carries `checkout_hash = b64url(sha-256(checkout_jwt))` and the `checkout_jwt` as a property disclosure; the closed Payment Mandate carries `transaction_id == checkout_hash`, `payee`, `payment_amount{amount(minor int),currency}`, `payment_instrument`, `execution_date`.
* Chain transport: `<open presentation>~<kb-jwt>~<closed disclosures>~` (the same concatenation the spec's encoded examples use). Parsers split on JWT-shaped segments.
* Verification (`verify_for_merchant`, `verify_for_payment`): trusted provider key by `kid`; `_sd_alg`; strict disclosure resolution (unreferenced disclosures rejected); `iat/exp` with 300 s skew; KB signature against `cnf.jwk`; `sd_hash` binding; `aud`; `checkout_hash` recomputed from the disclosed `checkout_jwt` and compared with the merchant's own JWT; open-mandate claims unchanged in the closed mandate; every constraint evaluated; **unknown constraint → `unresolved_constraint`**.
* `payment.reference` is checked by the credential provider against the open Checkout Mandate disclosure digest in the presented checkout chain (the agent includes the checkout chain in the payment presentation for that purpose).
* Receipts: JWT `{iss, iat, status: Success|Error, reference, order_id | payment_id | error, error_description}`, `reference` = `sd_hash`-style hash over the closed presentation. Merchant receipts are signed with the merchant key; payment receipts with the credential-provider key.
* Autonomous-flow rule "no second open-mandate presentation without a rejection receipt" is enforced in the coordinator through `authorizations.presentation_state` (`idle → pending → accepted | rejected`), and the credential-provider fixture refuses replay of a closed payment mandate.
* Inclusive maximum: the strict user rule "below $100" is translated to `payment.amount_range.max = 9999` (integer minor units) and shown to the user at review time.

## Verifiable Intent details (v0.1-draft)

* L1 issued by the fixture credential provider (`iss: https://issuer.fixture.pricewatch.local`, `vct: https://fixtures.pricewatch.local/credentials/card` — a **fixture credential profile**, not the Mastercard reference profile; it carries the same structural claims plus `pan_last_four`, `scheme`, `card_id`, and a selectively disclosable `email`). The wallet presents L1 *without* the email disclosure for delegation; L2 `sd_hash` binds that presentation.
* L2 (`typ: kb-sd-jwt+kb`, signed by the user device key bound in L1 `cnf`): `nonce, aud (agent), iss (wallet), iat, exp (= watch deadline, ≤30 days), sd_hash(L1), _sd_alg, _sd, delegate_payload`. Mandates are element disclosures referenced from `delegate_payload`; `_sd` holds the property disclosure `consent_context` (interpretation: RFC 9901 distinguishes property vs element disclosures, so the same digest cannot legally appear in both arrays as the draft's illustrative example shows).
* Autonomous mandates carry identical `cnf.jwk` (agent key **with `kid`**) and constraints; the payment mandate's `mandate.payment.reference.conditional_transaction_id` is the checkout mandate's disclosure digest (pair identifier).
* L3b/L3a (`typ: kb-sd-jwt`, header `kid` only — no self-asserted `jwk`; no `cnf`; ≤ 1 h lifetime, 5 min used) with *selective* `sd_hash` per §5.4: L3b over `L2_base~checkout~merchant~item~`, L3a over `L2_base~payment~payee~`. `L3a.transaction_id == L3b.checkout_hash == b64url(sha-256(checkout_jwt))`.
* Verification follows §3.5/§4.7/§5.7: issuer key by `kid`; `typ` per layer; `sd_hash` chain; `exp/iat` skew 300 s; orphan/duplicate pair rules when both mandates are disclosed; `kid` ↔ `cnf.jwk.kid`; machine-enforceable constraints evaluated (`allowed_merchants` from the `checkout_jwt` merchant `id`, `line_items` with `match_mode`, `amount_range` integer minor units, `allowed_payees`, `reference`); unknown `type` → `unresolved_constraint`. Fulfillment `line_items` must also equal the signed checkout (application check per plan §9).
* The network fixture enforces **one L3a+L3b pair per L2 mandate pair** (§5.7 rule 8); a later merchant rejection therefore consumes the VI authorization and the watch pauses for new consent, whereas an AP2 authorization can be re-presented after a rejection receipt.
* VI defines no receipt; the merchant fixture returns a `vi.verification` result and an **application-defined** `local.pricewatch.vi.merchant_attestation` JWT.

## Simulated pieces (real signing, simulated money)

* `CredentialProviderSim` = Credential Provider + Network (AP2) / payment network (VI): verifies the payment presentation, issues a signed payment credential (`typ: payment-credential+jwt`) scoped to `transaction_id`, amount and payee, and a signed Payment Receipt.
* `MerchantPaymentProcessorSim`: verifies the credential is scoped to the checkout before "capturing". No funds move.
* No provider sandbox/testnet path is wired (plan §4 "if supported"); adapters are isolated behind `CredentialProviderSim` / `MerchantPaymentProcessorSim`.

## Application-defined items (not protocol mandated)

Scheduling, leases, claims, product identity/SKU mapping, delivered-price evaluation, the offer feed, cancellation (online state check), notifications, reconciliation cases, the `merchant` field on checkout responses, the `local.pricewatch.*` handler family and credential types.
