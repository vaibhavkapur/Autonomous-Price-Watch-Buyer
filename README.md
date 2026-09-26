# Autonomous Price-Watch Buyer

An agent that watches one exact product and buys it once, within the user's explicit signed authority, even when the user is absent — and recovers safely from concurrent triggers and uncertain merchant outcomes.

> **[Read the full documentation](docs/setup.md)**

## Getting Started

```bash
# Install, test, and start the all-in-one server (SQLite, simulated clock)
make venv && make test
make dev

# Optional Next.js UI (separate terminal)
make web
```

Or `docker compose up --build` for PostgreSQL + API + worker + merchants + web. Authenticate with `Authorization: Bearer demo-token`.

## Quick Example

```bash
# Create a watch: buy this keyboard below $100 before Sunday, once, from two merchants
curl -X POST http://localhost:8000/v1/watches \
  -H "Authorization: Bearer demo-token" \
  -H "Idempotency-Key: watch-001" \
  -H "Content-Type: application/json" \
  -d '{
    "original_request": "Buy this exact keyboard if its delivered price drops below $100 before Sunday. Use only these two merchants and buy it once.",
    "product": { "product_id": "keyboard_k1_black_us", "title": "K1", "attributes": { "color": "black" } },
    "merchant_skus": { "merchant_a": "K1-BLK-US", "merchant_b": "KB-K1-US-B" },
    "price_rule": { "operator": "lt", "delivered_total_minor": 10000 },
    "allowed_merchants": ["merchant_a", "merchant_b"],
    "destination_id": "address_1",
    "expires_at": "2026-09-27T18:00:00+05:30",
    "timezone": "Asia/Kolkata",
    "authorization_profile": "ap2"
  }'

# Drop merchant A to a qualifying delivered total ($89 + $5 shipping + $4 tax) and evaluate now
curl -X POST http://localhost:8000/demo/merchants/merchant_a/price-scenario \
  -H "Authorization: Bearer demo-token" \
  -H "Content-Type: application/json" \
  -d '{"sku":"K1-BLK-US","item_price_minor":8900,"shipping_minor":500,"tax_minor":400}'

curl -X POST http://localhost:8000/demo/watches/{watch_id}/evaluate-now \
  -H "Authorization: Bearer demo-token"
```
