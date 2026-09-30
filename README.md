# Autonomous Price-Watch Buyer

An agent that watches one exact product and buys it once, within the user's explicit signed authority, even when the user is absent — and recovers safely from concurrent triggers and uncertain merchant outcomes.

> **[Read the full documentation](docs/index.md)**

Built with FastAPI, SQLAlchemy, and Next.js. Merchant and payment services are fixtures; the combined dev server uses a simulated clock.

## Getting Started

```bash
# Install, test, and start the all-in-one server (SQLite, simulated clock)
make venv && make test
make dev

# Optional Next.js UI (separate terminal)
make web
```

Or `docker compose up --build` for PostgreSQL + API + worker + merchants + web. Authenticate with `Authorization: Bearer demo-token`.

See [Getting Started](docs/getting-started.md) for prerequisites, cloning, configuration, and verification.

## Quick Example

```bash
# Create a draft watch with a deadline one day from now
WATCH_EXPIRES_AT=$(python3 -c 'from datetime import datetime,timedelta,timezone; print((datetime.now(timezone.utc)+timedelta(days=1)).isoformat())')
curl -X POST http://localhost:8000/v1/watches \
  -H "Authorization: Bearer demo-token" \
  -H "Idempotency-Key: watch-001" \
  -H "Content-Type: application/json" \
  -d @- <<EOF
  {
    "original_request": "Buy this exact keyboard if its delivered price drops below \$100 within the next day. Use only these two merchants and buy it once.",
    "product": { "product_id": "keyboard_k1_black_us", "title": "K1", "attributes": { "color": "black" } },
    "merchant_skus": { "merchant_a": "K1-BLK-US", "merchant_b": "KB-K1-US-B" },
    "price_rule": { "operator": "lt", "delivered_total_minor": 10000 },
    "allowed_merchants": ["merchant_a", "merchant_b"],
    "destination_id": "address_1",
    "expires_at": "$WATCH_EXPIRES_AT",
    "timezone": "Asia/Kolkata",
    "authorization_profile": "ap2"
  }
EOF

# Replace {watch_id} with the returned id; request and review the authorization
curl -X POST http://localhost:8000/v1/watches/{watch_id}/authorization-proposal \
  -H "Authorization: Bearer demo-token" -H "Idempotency-Key: proposal-001"
curl http://localhost:8000/v1/watches/{watch_id} -H "Authorization: Bearer demo-token"

# After review, replace VERSION with the latest watch version and activate
curl -X POST http://localhost:8000/v1/watches/{watch_id}/activate \
  -H "Authorization: Bearer demo-token" \
  -H "Idempotency-Key: activate-001" -H "If-Match: VERSION"

# Drop merchant A to a qualifying delivered total ($89 + $5 shipping + $4 tax) and evaluate now
curl -X POST http://localhost:8000/demo/merchants/merchant_a/price-scenario \
  -H "Authorization: Bearer demo-token" \
  -H "Content-Type: application/json" \
  -d '{"sku":"K1-BLK-US","item_price_minor":8900,"shipping_minor":500,"tax_minor":400}'

curl -X POST http://localhost:8000/demo/watches/{watch_id}/evaluate-now \
  -H "Authorization: Bearer demo-token"
```

Inspect `/v1/watches/{watch_id}/purchase` and `/v1/watches/{watch_id}/timeline` for the order and evidence. Use a future deadline relative to `clock.now` if the simulated server has been running for a while. See the [complete shell walkthrough](docs/setup.md).
