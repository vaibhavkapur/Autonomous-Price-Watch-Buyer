"""Run the test suite and write docs/test-report.md mapping plan §23 required
tests to the test functions that cover them, with pass/fail results."""
from __future__ import annotations

import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED = [
    ("$99.99 passes and $100.00 fails a strict $100 threshold", ["test_99_99_passes_and_100_00_fails_strict_100", "test_checkout_validation_boundaries_and_fields", "test_amount_range_ceiling_is_inclusive_max"]),
    ("missing shipping or tax prevents execution", ["test_missing_shipping_or_tax_prevents_execution", "test_feed_is_labelled_and_needs_a_destination_for_delivered_price", "test_checkout_create_verify_and_tamper_detection"]),
    ("wrong product variant is rejected", ["test_wrong_variant_and_wrong_sku_rejected", "test_sku_mapping_rejects_color_bundle_condition_layout", "test_wrong_merchant_or_item_is_outside_scope"]),
    ("merchant outside the allowlist is rejected", ["test_merchant_outside_allowlist_rejected", "test_wrong_merchant_or_item_is_outside_scope"]),
    ("duplicate scheduler messages are harmless", ["test_duplicate_scheduler_messages_are_harmless", "test_watch_is_polled_on_cadence_and_rescheduled_from_now"]),
    ("simultaneous qualifying merchants yield one execution claim", ["test_simultaneous_claims_yield_exactly_one_execution_claim", "test_database_index_blocks_second_active_claim_even_without_version_check", "test_two_workers_ticking_concurrently_produce_one_order", "test_demo_c_both_merchants_qualify_one_winner_one_order"]),
    ("stale worker lease cannot submit", ["test_stale_worker_lease_cannot_claim_or_submit", "test_only_one_worker_acquires_the_lease", "test_user_update_during_lease_invalidates_worker_version"]),
    ("cancellation wins before submission when serialized first", ["test_cancellation_wins_when_serialized_before_submission", "test_cancel_after_submission_is_reported_as_not_recallable"]),
    ("expired authorization blocks submission", ["test_expired_authorization_blocks_submission", "test_deadline_passing_between_claim_and_submission_blocks_submission", "test_expiry_is_decided_by_the_server_clock", "test_expired_authority_is_rejected"]),
    ("payment/order timeout preserves recovery state", ["test_demo_d_dropped_completion_response_recovers_without_buying_twice", "test_timeout_keeps_claim_and_blocks_other_merchant_until_reconciled", "test_merchant_5xx_before_processing_is_resolved_by_identical_resubmission", "test_restart_before_submission_releases_claim_safely"]),
    ("Demo A — deceptive item price", ["test_demo_a_deceptive_item_price"]),
    ("Demo B — valid drop", ["test_demo_b_valid_drop_purchases_once_with_verifiable_evidence"]),
    ("Demo C — concurrent opportunity", ["test_demo_c_concurrent_opportunity"]),
    ("Demo D — recovery", ["test_demo_d_recovery"]),
]


def main() -> None:
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-rA", "--tb=short", "-p", "no:cacheprovider"], cwd=ROOT, capture_output=True, text=True)
    out = proc.stdout + proc.stderr
    results = {}
    for line in out.splitlines():
        m = re.match(r"^(PASSED|FAILED|ERROR|SKIPPED) (\S+)", line)
        if m:
            results[m.group(2)] = m.group(1)
    counts = {}
    for v in results.values():
        counts[v] = counts.get(v, 0) + 1
    summary = ", ".join("%d %s" % (n, k.lower()) for k, n in sorted(counts.items())) or "no tests collected"

    def status_for(fn: str) -> str:
        hits = {k: v for k, v in results.items() if k.endswith("::" + fn) or ("::" + fn + "[") in k}
        if not hits:
            return "MISSING"
        return "PASS" if all(v == "PASSED" for v in hits.values()) else "FAIL"

    lines = ["# Test report", "", "Generated %s by `scripts/test_report.py` (`pytest -q -rA`)." % datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "", "**Summary:** `%s`" % summary, "", "## Plan §23 required tests → coverage", "", "| Requirement | Tests | Result |", "|---|---|---|"]
    for req, fns in REQUIRED:
        statuses = [status_for(f) for f in fns]
        overall = "PASS" if all(s == "PASS" for s in statuses) else ("MISSING" if "MISSING" in statuses else "FAIL")
        lines.append("| %s | %s | **%s** |" % (req, "<br>".join("`%s` (%s)" % (f, s) for f, s in zip(fns, statuses)), overall))
    lines += ["", "## All tests", "", "| Test | Result |", "|---|---|"]
    for k in sorted(results):
        lines.append("| `%s` | %s |" % (k, results[k]))
    (ROOT / "docs" / "test-report.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines[:4 + len(REQUIRED) + 6]))
    print("wrote docs/test-report.md")
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main()
