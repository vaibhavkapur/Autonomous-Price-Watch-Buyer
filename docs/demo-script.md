# Demo script (for the recorded walkthrough)

Recording itself is not produced by this repository; this is the shot list.
Every demo runs on the **simulated clock** shown in the top-right badge of the UI
and in `clock.simulated` on the API.

Preparation: `make dev` (API + merchants + worker) and `make web` (UI) or use the
`/docs` Swagger page. Log in with `demo-token`.

## Demo A — deceptive item price (≈1 min)

1. *Create watch*: paste “Buy this exact keyboard if its delivered price drops below $100 before Sunday. Use only these two merchants and buy it once.” → **Propose fields**. Show the notes: strict comparison, deadline resolved to Sunday 23:59:59 in the user's zone, "similar" ignored.
2. Select the canonical product, tick both merchants, pick the exact SKUs (note the bundle / white / refurbished / UK options are labelled as *not exact* and rejected server-side).
3. **Preview normalized rule** → sentence “largest qualifying delivered total is USD 99.99 … inclusive maximum of 9999”.
4. Create → *Authorization review*: constraints table (acceptable items, merchants, amount range 0…9999, authority end, agent key). Approve.
5. *Watch detail*: demo controls → merchant_a item 89.00 / shipping 12.00 / tax 4.00 → **Evaluate now**. The offer row shows item 89.00 but delivered total **105.00**, verdict *rejected*, reason `price_rule_not_met total=10500`.

## Demo B — valid drop (≈1 min)

1. Same watch. Set shipping 5.00 → **Evaluate now**.
2. Decision history fills: `candidate.found` → `checkout.created` → `checkout.validated` (98.00) → `authorization.prepared` → `purchase.claimed` → `payment.authorization ok` → `purchase.submitting` → `purchase.completed order_id=…`.
3. *Purchase detail*: claim *purchased*, order id + permalink, payment *captured*, idempotency key, outbound event *acknowledged*, evidence bundle. Inspect `ap2.checkout_receipt` (or `vi.merchant_result`) and `ap2.payment_receipt`. Note credentials are redacted.

## Demo C — concurrent opportunity (≈1 min)

1. New watch (both merchants). Set merchant_a to 89/5/4 (98.00) and merchant_b to 87/6/4 (97.00) *before* evaluating.
2. **Evaluate now** → `candidate.found.ranking` lists merchant_b first; one order at merchant_b, none at merchant_a (`GET /demo/merchants/merchant_a/orders` is empty). Metrics: purchases 1.
3. Optional: run `pytest tests/concurrency -q` on screen to show the threaded claim race and the DB index test.

## Demo D — recovery (≈2 min)

1. New watch (both merchants), both eligible. `POST /demo/faults {"target":"adapter","fault":"drop_complete_response","count":1}`.
2. **Evaluate now** → outcome `reconciliation_required`. Watch status *reconciliation required*; purchase detail shows claim retained, outbound event *sent_unknown*, an **open** reconciliation case, notification “Purchase outcome uncertain”. `GET /demo/merchants/merchant_a/orders` already shows one order — the agent just never saw the response.
3. Restart the worker (`Ctrl-C` `make worker` and start again, or in the dev server let the next tick run the reconciler). Timeline gains `reconciliation.checked checkout_status=completed` → `purchase.completed resolution=recovered_via_get_checkout`. Orders: merchant_a 1, merchant_b 0. Metric `unresolved_executions` back to 0 open cases.

## Extras worth 20 seconds each

* Cancel during evaluation → `purchase.aborted_before_submission reason=cancelled_before_submission`.
* `+1 day` clock button → watch *expired*, authorization *expired*, notification.
* Switch the profile to Verifiable Intent on a new watch and repeat Demo B: evidence kinds change to `vi.consent`, `vi.prepared_presentation` (L1/L2 views/L3a/L3b), `vi.merchant_result`.
