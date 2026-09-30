# Testing

[Documentation home](index.md)

## Backend and demos

```bash
make venv
make test
make demo
PYTHONPATH=packages:apps:tests .venv/bin/python scripts/run_demo.py vi --out docs/evidence
```

The suite covers price boundaries, product identity, scheduling, API ownership, protocol bindings, concurrent claims, and recovery. `make demo` exercises AP2 by default; the second command selects VI and writes evidence files.

`make test-report` regenerates the tracked test report. Treat [Test Report](test-report.md) as a dated record, not proof that tests ran on every later commit. Current command output is authoritative.

## UI

```bash
cd apps/web
npm ci
npx tsc --noEmit
npm run build
```

## Expected invariants

A strict $100 threshold excludes $100.00, missing tax or shipping blocks execution, and wrong variants fail identity checks. Concurrent qualifying merchants must produce one active claim. A dropped completion response must keep recovery state and resolve the existing order before another purchase is considered.

Use [Demo Script](demo-script.md) and [Evidence Bundle](evidence-bundle.md) to inspect those results. No live merchant, issuer, or payment network is covered by the local fixture suite.
