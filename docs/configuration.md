# Configuration

[Documentation home](index.md)

## Loading settings

`packages/common/config.py` reads environment variables. Export values before starting processes; `.env.example` is a reference, not automatically loaded by Python. The Makefile supplies `PYTHONPATH=packages:apps`.

## Runtime and data

- `APP_PROFILE`: `development`; demo routes also require `DEMO_CONTROLS` (default true).
- `DATABASE_URL`: SQLite at `<repo>/data/pricewatch.sqlite3`; Compose uses PostgreSQL with `psycopg`.
- `DEV_KEYS_PATH`: `<repo>/fixtures/authorizations/dev_keys.json`; generated for fixtures and shared by separate processes.
- `ARTIFACT_ENCRYPTION_KEY`: default empty setting; configure consistent material when retaining protected evidence. Compose supplies a development value.
- `DEV_SIMULATED_CLOCK`: `1` in the combined dev server; set `0` there for system time. Separate API/worker processes use their normal bootstrap clock.
- `WORKER_TICK_SECONDS`: `2` by default; distinct from the per-watch polling interval.

## Scheduling defaults

- `POLL_INTERVAL_SECONDS`: `60` (Compose overrides to `30`).
- `POLL_JITTER_SECONDS`: `10`.
- `LEASE_SECONDS`: `120`.
- `OBSERVATION_FRESHNESS_SECONDS`: `300`.
- `MERCHANT_MIN_INTERVAL_MS`: `500`.
- `MERCHANT_BACKOFF_BASE_SECONDS` / `MERCHANT_BACKOFF_MAX_SECONDS`: `30` / `1800`.
- `HTTP_TIMEOUT_SECONDS`: `10`.

The database is the schedule; this project does not require Redis. Leases and authorization deadlines are checked independently of polling cadence.

## Addresses

`MERCHANT_A_URL` and `MERCHANT_B_URL` default to `http://localhost:8101` and `http://localhost:8102`. `API_CORS_ORIGINS` defaults to `http://localhost:3000`. `AGENT_ID` defaults to `agent_pricewatch_1`; `AGENT_PLATFORM_URL` to `https://agent.pricewatch.local`.

Compose supports `POSTGRES_PORT`, `API_PORT`, `WEB_PORT`, `MERCHANT_A_PORT`, and `MERCHANT_B_PORT` host overrides. Set `PUBLIC_API_URL` to the browser-reachable API address when changing the API port; it is passed as the web build's `NEXT_PUBLIC_API_URL`. Change `API_CORS_ORIGINS` alongside a web-port change.
