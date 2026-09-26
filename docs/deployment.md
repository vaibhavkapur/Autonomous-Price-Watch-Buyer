# Deployment

[Documentation home](index.md)

## Combined local server

`make dev` runs the API, merchant fixtures, and worker with SQLite. `make web` starts the optional UI separately. This mode uses an explicitly simulated clock by default; see [Configuration](configuration.md).

## Separate services

For separate local processes, run `make merchant-a`, `make merchant-b`, `make api`, `make worker`, and `make web` in their own terminals after `make venv`. They share `DATABASE_URL` and `DEV_KEYS_PATH`.

## Docker Compose

```bash
docker compose up --build
docker compose ps
```

Compose starts PostgreSQL, a one-shot migration/key initializer, two merchants (`8101`, `8102`), API (`8000`), worker, and web (`3000`). It mounts `pgdata` and `devkeys`. The API/worker share PostgreSQL and use HTTP merchant endpoints.

If ports conflict:

```bash
POSTGRES_PORT=5433 API_PORT=8002 WEB_PORT=3001 MERCHANT_B_PORT=8103 \
  PUBLIC_API_URL=http://localhost:8002 API_CORS_ORIGINS=http://localhost:3001 \
  docker compose up --build
```

Use `docker compose logs api worker` for evaluation and recovery, and `GET /v1/metrics` for open reconciliation cases. `docker compose stop` preserves containers and volumes; `make compose-down` invokes `down -v` and deletes named data volumes, so it is not a routine pause command.

## Scope and recovery

The setup uses synthetic identities, generated development keys, UCP merchant fixtures, and simulated payment processing. `APP_PROFILE` and `DEMO_CONTROLS` gate demo routes but do not supply production identity or payment integration. Merchant fixtures hold process-local order state, so a complete stack restart has different evidence limits from a worker-only restart. See [Database](database.md) and [Protocol Manifest](protocol-manifest.md).
