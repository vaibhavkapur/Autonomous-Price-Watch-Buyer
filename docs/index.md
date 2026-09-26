# Autonomous Price-Watch Buyer

An exact-product watcher that can buy once within reviewed user authority and reconcile an uncertain merchant outcome before another purchase is considered.

[Get Started](getting-started.md) · [API Reference](api-reference.md) · [Repository README](../README.md)

## Key Features

- Exact product/SKU matching and delivered-price rules including shipping and tax.
- Reviewed AP2 or VI authority, authoritative UCP checkout revalidation, and a single active claim.
- Database scheduling, worker leases, concurrency checks, and redacted evidence bundles.

## Tech Stack and Scope

Python / FastAPI / SQLAlchemy; SQLite or PostgreSQL; Next.js UI. Local merchants, issuer keys, and payment processing are fixtures. The combined development server uses a simulated clock.

## Documentation

- [Getting Started](getting-started.md)
- [Architecture](architecture.md)
- [API Reference](api-reference.md)
- [Configuration](configuration.md)
- [Database and Evidence](database.md)
- [Testing](testing.md)
- [Deployment](deployment.md)
- [Setup Walkthrough](setup.md)
- [Protocol Manifest](protocol-manifest.md)
- [State Machines](state-machines.md)
- [Evidence Bundle](evidence-bundle.md)
- [Demo Script](demo-script.md)
- [Recorded Test Report](test-report.md)

## Project Structure

- `apps/`: API, worker, two merchants, combined development server, and web UI.
- `packages/`: watch rules, identity, scheduler, UCP adapter, authority, execution, and persistence.
- `scripts/`, `fixtures/`, `migrations/`, `tests/`: demos, synthetic products, schema, and tests.

The implementation guides describe the current code. [Development plan](../plan.md) records design intent and future work; planned features are not automatically implemented.

## Related projects

These are independent companion repositories, not runtime dependencies or claims of an implemented integration:

- [Cross-Border Payments Engine](https://github.com/vaibhavkapur/Cross-Border-Payments-Engine): remittance quoting, settlement lifecycle, and ledger demonstration.
- [Stablecoin Payments API](https://github.com/vaibhavkapur/Stablecoin-Payments-API): customer, wallet, deposit, transfer, and checkout API.
- [Agentic Commerce + Stablecoin Checkout](https://github.com/vaibhavkapur/Agentic-Commerce-Stablecoin-Checkout): conversational commerce, policy checks, and payment routing.
- [Smart Wallet Policy Engine](https://github.com/vaibhavkapur/smart-wallet-policy-engine): transaction risk evaluation and wallet authorization.
- [Stablecoin Payment Orchestrator](https://github.com/vaibhavkapur/Stablecoin-Payment-Orchestrator): USDC routing, workers, and treasury accounting.
