# API Reference

[Documentation home](index.md)

## Base URL and authentication

Local base URL: `http://localhost:8000`. Watch routes are implemented in `apps/api/main.py`. Send `Authorization: Bearer demo-token`; watch reads and mutations enforce user ownership. See [Getting Started](getting-started.md) for the standalone API explorer.

## Create and authorize

- `GET /v1/catalog`: canonical products, exact merchant SKUs, and supported profiles.
- `GET /v1/destinations`: user destinations.
- `POST /v1/watches/parse`: `text` and `timezone`; proposes structured fields without creating a watch.
- `POST /v1/watches/preview`: validates a draft and renders its normalized price rule.
- `POST /v1/watches`: creates a draft, returning 201 and the watch `id` and `version`.
- `POST /v1/watches/{watch_id}/authorization-proposal`: prepares the consent review.
- `POST /v1/watches/{watch_id}/activate`: signs the reviewed authorization and activates polling.

Creation and authorization-proposal requests require `Idempotency-Key`. Activation also requires `If-Match: VERSION` (or `expected_version` in the mutation body). Read the current watch version after reviewing the proposal.

```bash
curl -X POST http://localhost:8000/v1/watches/WATCH_ID/authorization-proposal \
  -H "Authorization: Bearer demo-token" -H "Idempotency-Key: proposal-001"

curl http://localhost:8000/v1/watches/WATCH_ID \
  -H "Authorization: Bearer demo-token"

# After reviewing the proposal, replace VERSION with the latest version
curl -X POST http://localhost:8000/v1/watches/WATCH_ID/activate \
  -H "Authorization: Bearer demo-token" \
  -H "Idempotency-Key: activate-001" -H "If-Match: VERSION"
```

Draft fields and a complete example appear in the [Setup walkthrough](setup.md). Amounts are integer USD minor units; for a strict threshold of `10000`, a delivered total of `10000` fails and `9999` passes if all other constraints pass. Expiry must include a timezone and be future relative to the server clock.

## Lifecycle and evidence

- `GET /v1/watches` and `GET /v1/watches/{watch_id}`: state, version, active authority, and active attempt.
- `POST /v1/watches/{watch_id}/pause`, `/resume`, `/cancel`: require idempotency and version headers like activation.
- `GET /v1/watches/{watch_id}/observations`: price observations and display totals.
- `GET /v1/watches/{watch_id}/timeline`: events and evaluation runs.
- `GET /v1/watches/{watch_id}/purchase`: purchase and recovery state.
- `GET /v1/watches/{watch_id}/evidence/{artifact_id}`: owned, redacted artifact view.
- `GET /v1/metrics`: metrics, reconciliation cases, and clock state.

## Development controls

When both the development profile and demo controls are enabled, `/demo/merchants/{merchant_id}/price-scenario` sets prices, `/demo/watches/{watch_id}/evaluate-now` triggers evaluation, `/demo/faults` injects faults, and `/demo/clock/advance?seconds=3600` advances an injectable clock. These routes use the bearer token but do not require watch-mutation idempotency headers. In separate-process mode evaluation schedules work for the worker's next tick.

## Errors and retry behavior

Missing idempotency or version preconditions return 428; reuse of an idempotency key with a changed body returns 409. Draft validation returns 422. Service errors use `code` and `message`; FastAPI HTTP exceptions wrap details under `detail`, so clients must handle both shapes.

Retry the same operation with its original key and body. An uncertain checkout is reconciled through the existing attempt, not a new watch or another merchant purchase. See [State Machines](state-machines.md).
