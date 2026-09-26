"""Run demos A–D end to end against the in-process harness and print a
human-readable decision history plus a redacted evidence bundle.

    PYTHONPATH=packages:apps:tests python scripts/run_demo.py [ap2|vi] [--out docs/evidence]
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in ("packages", "apps", "tests"):
    sys.path.insert(0, str(ROOT / p))

from authorization_profiles import sdjwt  # noqa: E402
from common.money import minor_to_display  # noqa: E402
from harness import Harness  # noqa: E402
from persistence import repo  # noqa: E402

SCENARIOS = ROOT / "fixtures" / "price-scenarios"


def redact(obj):
    if isinstance(obj, dict):
        return {k: ("<redacted-credential>" if k in ("token", "credential") and isinstance(v, str) else redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    if isinstance(obj, str) and len(obj) > 200:
        return obj[:64] + "…<%d chars sha256=%s>" % (len(obj), sdjwt.sha256_b64url(obj.encode())[:16])
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj


def apply(h, sc):
    for step in sc["steps"]:
        h.set_price(step["merchant"], step["sku"], **{k: v for k, v in step.items() if k not in ("merchant", "sku")})
    for f in sc.get("faults", []):
        if f["target"] == "adapter":
            h.add_adapter_fault(f["fault"], f["count"])
        else:
            h.merchant_clients[f["target"]].post("/demo/faults", json={"fault": f["fault"], "count": f["count"]})


def print_history(h, watch_id):
    for e in h.events(watch_id):
        d = e["details"]
        short = {k: d[k] for k in ("reason", "total_minor", "feed_total_minor", "order_id", "merchant_id", "reasons", "ranking", "ok", "outcome") if k in d}
        print("  %s  %-40s %s" % (e["at"].strftime("%H:%M:%S"), e["type"], json.dumps(short, default=str)[:140]))
    print("  observations:")
    for o in reversed(h.service.observations(watch_id)):
        pc = o["price_components"]
        print("    %-10s %-14s item=%s ship=%s tax=%s total=%s → %s %s" % (o["merchant_id"], "checkout" if o["source_reference"].startswith("ucp") else "feed", pc["item_subtotal"], pc["shipping"], pc["tax"], minor_to_display(o["total_minor"]) if o["total_minor"] is not None else "—", o["eligibility"], "; ".join(o["reasons"])[:80]))


def main():
    profile = next((a for a in sys.argv[1:] if a in ("ap2", "vi")), "ap2")
    out_dir = None
    if "--out" in sys.argv:
        out_dir = Path(sys.argv[sys.argv.index("--out") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)
    bundle = {"profile": profile, "demos": {}}

    for name, file in (("A", "demo_a_deceptive_item_price.json"), ("B", "demo_b_valid_drop.json"), ("C", "demo_c_concurrent.json"), ("D", "demo_d_recovery.json")):
        sc = json.loads((SCENARIOS / file).read_text())
        h = Harness()
        try:
            kwargs = {"authorization_profile": profile}
            if name in ("A", "B"):
                kwargs.update(allowed_merchants=["merchant_a"], merchant_skus={"merchant_a": "K1-BLK-US"})
            w = h.create_active_watch(**kwargs)
            apply(h, sc)
            print("\n=== Demo %s — %s [%s] ===" % (name, sc["name"], profile))
            print(sc["description"])
            results = h.tick()
            if name == "D":
                print("  first worker outcome:", results[0]["outcome"])
                restarted = h.new_worker("worker-after-restart")
                print("  restart → recovery:", restarted.start())
            print_history(h, w["id"])
            final = h.watch(w["id"])
            orders = {m: len(h.orders(m)) for m in h.merchants}
            print("  final status: %s   orders: %s" % (final["status"], orders))
            with h.engine.begin() as conn:
                arts = h.ctx.vault.load_many(conn, watch_id=w["id"])
                metrics = repo.all_metrics(conn)
            bundle["demos"][name] = {
                "scenario": sc,
                "final_status": final["status"],
                "orders": orders,
                "metrics": metrics,
                "timeline": [{"at": e["at"].isoformat(), "type": e["type"], "details": redact(e["details"])} for e in h.events(w["id"])],
                "evidence": {aid: {"kind": a["kind"], "digest": a["digest"], "content": redact(a["content"])} for aid, a in arts.items()},
            }
        finally:
            h.close()
    if out_dir:
        target = out_dir / ("evidence-bundle-%s.json" % profile)
        target.write_text(json.dumps(bundle, indent=1, default=str))
        print("\nwrote redacted evidence bundle →", target)


if __name__ == "__main__":
    main()
