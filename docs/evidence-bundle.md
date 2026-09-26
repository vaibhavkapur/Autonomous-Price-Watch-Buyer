# Evidence bundle

`docs/evidence/evidence-bundle-ap2.json` and `docs/evidence/evidence-bundle-vi.json`
are produced by `scripts/run_demo.py <profile> --out docs/evidence`. Each bundle
holds, per demo A–D: the price scenario, the final watch status, per-merchant order
counts, metrics, the full decision history (`watch_events`) and every protected
artifact stored for the watch with its digest.

Redaction rules (`redact()` in the script and `_redact()` in the API's evidence
endpoint):

* payment credential tokens are replaced with `<redacted-credential>` / a digest
  prefix;
* strings longer than 200 characters (SD-JWT chains, checkout JWTs) are truncated
  and annotated with length and SHA-256 prefix, so structure is visible while
  nothing in the bundle is replayable;
* the untruncated artifacts remain Fernet-encrypted in `protocol_artifacts` and
  are only decrypted for the owning user via `GET /v1/watches/{id}/evidence/{artifact_id}`.

## What an observer can verify from the bundle

| Question | Evidence |
|---|---|
| What exactly was delegated? | `<profile>.consent` artifact (open mandates / L2 with constraints), `authorization.granted` event `summary` (allowed merchants, acceptable items, quantity, inclusive max in minor units, deadline, agent key id) |
| Why was the $89 offer rejected? | Demo A `offer_observations`: `item_subtotal=8900, shipping=1200, tax=400, delivered_total=10500`, reason `price_rule_not_met total=10500 operator=lt threshold=10000` |
| Which offer won and why? | `candidate.found.ranking` (delivered totals, deterministic order), `checkout.validated` (final UCP totals vs feed) |
| Was the final checkout the one authorised? | `<profile>.prepared_presentation.checkout_hash` == hash of the merchant-signed `checkout_jwt`; merchant `verification_log` in the fixture; AP2 receipt `reference` == hash of the closed presentation |
| Did payment authority match? | `<profile>.payment_receipt` (`status: Success`, `reference`), `payment.authorization` event |
| Was exactly one order placed? | `orders` per merchant, `purchase_attempts` (one active claim), `duplicate_claims_prevented` metric |
| How was the lost response recovered? | Demo D: `purchase.outcome_unknown` → `reconciliation.checked` (`checkout_status: completed`, `order_id`) → `purchase.completed` with `resolution: recovered_via_get_checkout`; `reconciliation_cases` resolved `order_found`; `outbound_events` keeps `sent_unknown` until then |

## Verifying receipts independently

Receipts and attestations are ES256 JWTs. Public keys:

* merchants — `GET /.well-known/ucp` → `keys[]` (or `/.well-known/jwks.json`) on each merchant fixture;
* credential provider — `trust_store` role `credential_provider` (development key material in `fixtures/authorizations/dev_keys.json`; public part only is needed).

`tests/protocol/test_demo_scenarios.py::test_demo_b_valid_drop_purchases_once_with_verifiable_evidence`
performs this verification programmatically for both profiles.
