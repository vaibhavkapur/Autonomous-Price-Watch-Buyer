# Getting Started

[Documentation home](index.md)

## Prerequisites and installation

Use Python 3.9+ and Make. The optional web UI needs Node.js 20+ and npm. The container runtime is Python 3.12.

```bash
git clone https://github.com/vaibhavkapur/Autonomous-Price-Watch-Buyer.git
cd Autonomous-Price-Watch-Buyer
make venv
make test
make dev
```

This starts the API, both merchant fixtures, and a worker thread with SQLite and a simulated clock. `make dev` stays running. In a second terminal, run `make web` from the repository root to start [the UI](http://localhost:3000).

The API uses `Authorization: Bearer demo-token`. `GET /v1/catalog` lists products, merchant SKUs, and authorization profiles. `GET /v1/destinations` lists the demo user's destinations.

## First purchase

1. Create a draft watch for `keyboard_k1_black_us`, merchant A SKU `K1-BLK-US`, merchant B SKU `KB-K1-US-B`, and destination `address_1`.
2. Set a strict delivered-price threshold of `10000` USD minor units and a future expiry relative to the server clock.
3. Request the authorization proposal and review the normalized constraints. A strict “below $100” rule becomes an inclusive authorization ceiling of $99.99.
4. Activate the watch with the latest version and an idempotency key. A draft watch does not purchase.
5. Set merchant A's item/shipping/tax to `8900` / `500` / `400` and evaluate. The authoritative delivered total is $98.
6. Inspect purchase state, order ID, timeline, and redacted authorization/payment evidence.

The [API Reference](api-reference.md) gives required headers. The [Setup walkthrough](setup.md) contains the shell flow, while [Demo Script](demo-script.md) covers the UI.

## Clock and API explorer

`make dev` freezes the simulated clock at process startup; `/demo/clock/advance` advances it. Inspect `clock.now` on a watch response before reusing saved examples. A calendar date from an old demo can be expired.

The combined dev server mounts the API at `/`. Its root `/docs` describes the parent app, so it is not the complete watch API explorer. To use the application Swagger UI at `http://localhost:8000/docs`, stop `make dev` and follow the separate-process setup (`make merchant-a`, `make merchant-b`, `make api`, `make worker`, in separate terminals).

## Alternative and verification

`docker compose up --build` starts the PostgreSQL topology described in [Deployment](deployment.md). `make demo` runs the AP2 demos headlessly; [Testing](testing.md) also covers VI and evidence export. Merchants and payment processing are fixtures.
