# Setup

[Documentation home](index.md)

## Requirements

* Python 3.9+ (developed and tested on 3.9.6; containers use 3.12)
* Optional: Node 20+ for the Next.js UI, Docker for the composed environment

## 1. Python environment

```bash
make venv                 # creates .venv and installs runtime + test dependencies
make test                 # 79 tests: rules, scheduling, protocol, concurrency, recovery, api
make test-report          # regenerates docs/test-report.md
```

`pyproject.toml` sets `pythonpath = ["packages", "apps", "."]` for pytest; for
scripts use `export PYTHONPATH=packages:apps` (the Makefile does this).

## 2. Fastest demo: all-in-one dev server (no Docker, no Node)

```bash
make dev                  # combined server on http://localhost:8000
```

One process hosts the API, both UCP merchant fixtures (mounted at
`/merchants/merchant_a`, `/merchants/merchant_b`) and a worker thread over a
SQLite database (`data/pricewatch.sqlite3`), on a **simulated clock** (labelled
in every API response under `clock.simulated`). Development keys are generated
once into `fixtures/authorizations/dev_keys.json` (git-ignored).

Authenticate with `Authorization: Bearer demo-token` (user `user_demo`,
destination `address_1`).

Drive Demo B from the shell on a newly started clock. For a reused simulated server, choose an expiry relative to `clock.now` instead of the host clock:

```bash
H='Authorization: Bearer demo-token'
WATCH_EXPIRES_AT=$(python3 -c 'from datetime import datetime,timedelta,timezone; print((datetime.now(timezone.utc)+timedelta(days=1)).isoformat())')
# 1. create a draft
W=$(curl -s -X POST localhost:8000/v1/watches -H "$H" -H 'Idempotency-Key: c1' -H 'Content-Type: application/json' -d @- <<EOF | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])'
{"original_request":"Buy this exact keyboard if its delivered price drops below \$100 within the next day. Use only these two merchants and buy it once.",
 "product":{"product_id":"keyboard_k1_black_us","title":"K1","attributes":{"color":"black"}},
 "merchant_skus":{"merchant_a":"K1-BLK-US","merchant_b":"KB-K1-US-B"},
 "price_rule":{"operator":"lt","delivered_total_minor":10000},
 "allowed_merchants":["merchant_a","merchant_b"],"destination_id":"address_1",
 "expires_at":"$WATCH_EXPIRES_AT","timezone":"Asia/Kolkata","authorization_profile":"ap2"}
EOF
)
# 2. review the proposal (what will be signed), then approve
curl -s -X POST localhost:8000/v1/watches/$W/authorization-proposal -H "$H" -H 'Idempotency-Key: p1' | python3 -m json.tool | head -40
V=$(curl -s localhost:8000/v1/watches/$W -H "$H" | python3 -c 'import sys,json; print(json.load(sys.stdin)["version"])')
curl -s -X POST localhost:8000/v1/watches/$W/activate -H "$H" -H "If-Match: $V" -H 'Idempotency-Key: a1'
# 3. price drop: $89 + $5 shipping + $4 tax = $98 delivered
curl -s -X POST localhost:8000/demo/merchants/merchant_a/price-scenario -H "$H" -H 'Content-Type: application/json' \
  -d '{"sku":"K1-BLK-US","item_price_minor":8900,"shipping_minor":500,"tax_minor":400}'
curl -s -X POST localhost:8000/demo/watches/$W/evaluate-now -H "$H"
# 4. inspect
curl -s localhost:8000/v1/watches/$W/timeline -H "$H" | python3 -m json.tool | grep '"type"'
curl -s localhost:8000/v1/watches/$W/purchase -H "$H" | python3 -m json.tool | head -60
```

Other demo controls: `POST /demo/faults {"target":"adapter","fault":"drop_complete_response","count":1}`
(Demo D), `POST /demo/clock/advance?seconds=3600`, `GET /demo/merchants/{id}/orders`.
Demo routes exist only when `APP_PROFILE=development`.

Or run all four demos headlessly and emit a redacted evidence bundle:

```bash
make demo                                         # AP2
PYTHONPATH=packages:apps:tests .venv/bin/python scripts/run_demo.py vi --out docs/evidence
```

The combined server's root `/docs` belongs to its parent app and does not list the mounted watch routes. The separate API below serves the complete application Swagger UI at `/docs`.

## 3. Separate processes

```bash
make merchant-a   # :8101   make merchant-b   # :8102
make api          # :8000   make worker
make web          # :3000 (Next.js; needs Node)
```

All processes share `DATABASE_URL` and `DEV_KEYS_PATH`. Merchants only read
their own private key and the public trust store from that file.

## 4. Docker Compose (PostgreSQL)

```bash
docker compose up --build
```

Services: `postgres`, `migrate` (applies `migrations/001_initial.sql` and writes
shared development keys), `merchant-a`, `merchant-b`, `api` (:8000), `worker`,
`web` (:3000). The web image is built from `apps/web/Dockerfile`; set
`PUBLIC_API_URL` if the API is not on `localhost:8000`.

If the default host ports are already taken, remap them (the recorded development run used
5433 / 8002 / 3001 / 8103 because other compose stacks held 5432 / 8000 / 3000 / 8102):

```bash
POSTGRES_PORT=5433 API_PORT=8002 WEB_PORT=3001 MERCHANT_B_PORT=8103 \
  PUBLIC_API_URL=http://localhost:8002 API_CORS_ORIGINS=http://localhost:3001 \
  docker compose up --build
```

`migrations/001_initial.sql` is generated from the SQLAlchemy schema with
`make migration`; the partial unique index
`uq_purchase_attempts_active_claim` is what makes the single-claim guarantee a
database property on both PostgreSQL and SQLite.

## 5. Configuration

See `.env.example`. Notable knobs: `POLL_INTERVAL_SECONDS`, `POLL_JITTER_SECONDS`,
`LEASE_SECONDS`, `OBSERVATION_FRESHNESS_SECONDS`, `MERCHANT_MIN_INTERVAL_MS`,
`MERCHANT_BACKOFF_BASE_SECONDS`, `ARTIFACT_ENCRYPTION_KEY` (Fernet key derivation
for protected artifacts), `DEMO_CONTROLS`.

## 6. Recorded verification status (2026-09-26)

* Backend, protocol fixtures, worker, API and demos are exercised by the test
  suite (`docs/test-report.md`) on Python 3.9 / SQLite.
* Next.js UI: `npm install`, `tsc --noEmit` and `next build` succeed locally
  (Node 20.20.2). The compose `web` image also builds (`output: standalone`).
* Docker Compose on PostgreSQL 16 was brought up on this machine (migrate
  applied `001_initial.sql`, including `uq_purchase_attempts_active_claim`).
  A browser pass created a watch, approved AP2 authorization, and the worker
  completed one purchase after a qualifying price drop ($89 + $5 shipping + $4
  tax = $98 delivered). Host ports were remapped as above because 3000 / 5432 /
  8000 / 8102 were already in use by other local stacks.
