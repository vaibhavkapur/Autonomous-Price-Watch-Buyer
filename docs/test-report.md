# Test report

Generated 2026-09-26 12:33 UTC by `scripts/test_report.py` (`pytest -q -rA`).

**Summary:** `79 passed`

## Plan §23 required tests → coverage

| Requirement | Tests | Result |
|---|---|---|
| $99.99 passes and $100.00 fails a strict $100 threshold | `test_99_99_passes_and_100_00_fails_strict_100` (PASS)<br>`test_checkout_validation_boundaries_and_fields` (PASS)<br>`test_amount_range_ceiling_is_inclusive_max` (PASS) | **PASS** |
| missing shipping or tax prevents execution | `test_missing_shipping_or_tax_prevents_execution` (PASS)<br>`test_feed_is_labelled_and_needs_a_destination_for_delivered_price` (PASS)<br>`test_checkout_create_verify_and_tamper_detection` (PASS) | **PASS** |
| wrong product variant is rejected | `test_wrong_variant_and_wrong_sku_rejected` (PASS)<br>`test_sku_mapping_rejects_color_bundle_condition_layout` (PASS)<br>`test_wrong_merchant_or_item_is_outside_scope` (PASS) | **PASS** |
| merchant outside the allowlist is rejected | `test_merchant_outside_allowlist_rejected` (PASS)<br>`test_wrong_merchant_or_item_is_outside_scope` (PASS) | **PASS** |
| duplicate scheduler messages are harmless | `test_duplicate_scheduler_messages_are_harmless` (PASS)<br>`test_watch_is_polled_on_cadence_and_rescheduled_from_now` (PASS) | **PASS** |
| simultaneous qualifying merchants yield one execution claim | `test_simultaneous_claims_yield_exactly_one_execution_claim` (PASS)<br>`test_database_index_blocks_second_active_claim_even_without_version_check` (PASS)<br>`test_two_workers_ticking_concurrently_produce_one_order` (PASS)<br>`test_demo_c_both_merchants_qualify_one_winner_one_order` (PASS) | **PASS** |
| stale worker lease cannot submit | `test_stale_worker_lease_cannot_claim_or_submit` (PASS)<br>`test_only_one_worker_acquires_the_lease` (PASS)<br>`test_user_update_during_lease_invalidates_worker_version` (PASS) | **PASS** |
| cancellation wins before submission when serialized first | `test_cancellation_wins_when_serialized_before_submission` (PASS)<br>`test_cancel_after_submission_is_reported_as_not_recallable` (PASS) | **PASS** |
| expired authorization blocks submission | `test_expired_authorization_blocks_submission` (PASS)<br>`test_deadline_passing_between_claim_and_submission_blocks_submission` (PASS)<br>`test_expiry_is_decided_by_the_server_clock` (PASS)<br>`test_expired_authority_is_rejected` (PASS) | **PASS** |
| payment/order timeout preserves recovery state | `test_demo_d_dropped_completion_response_recovers_without_buying_twice` (PASS)<br>`test_timeout_keeps_claim_and_blocks_other_merchant_until_reconciled` (PASS)<br>`test_merchant_5xx_before_processing_is_resolved_by_identical_resubmission` (PASS)<br>`test_restart_before_submission_releases_claim_safely` (PASS) | **PASS** |
| Demo A — deceptive item price | `test_demo_a_deceptive_item_price` (PASS) | **PASS** |
| Demo B — valid drop | `test_demo_b_valid_drop_purchases_once_with_verifiable_evidence` (PASS) | **PASS** |
| Demo C — concurrent opportunity | `test_demo_c_concurrent_opportunity` (PASS) | **PASS** |
| Demo D — recovery | `test_demo_d_recovery` (PASS) | **PASS** |

## All tests

| Test | Result |
|---|---|
| `tests/api/test_api.py::test_authentication_and_ownership` | PASSED |
| `tests/api/test_api.py::test_cancel_and_pause_resume_via_api` | PASSED |
| `tests/api/test_api.py::test_demo_controls_disabled_outside_development` | PASSED |
| `tests/api/test_api.py::test_parse_preview_create_propose_activate_flow` | PASSED |
| `tests/concurrency/test_single_purchase.py::test_cancel_after_submission_is_reported_as_not_recallable` | PASSED |
| `tests/concurrency/test_single_purchase.py::test_cancellation_wins_when_serialized_before_submission` | PASSED |
| `tests/concurrency/test_single_purchase.py::test_database_index_blocks_second_active_claim_even_without_version_check` | PASSED |
| `tests/concurrency/test_single_purchase.py::test_deadline_passing_between_claim_and_submission_blocks_submission` | PASSED |
| `tests/concurrency/test_single_purchase.py::test_demo_c_both_merchants_qualify_one_winner_one_order` | PASSED |
| `tests/concurrency/test_single_purchase.py::test_expired_authorization_blocks_submission` | PASSED |
| `tests/concurrency/test_single_purchase.py::test_presented_mandate_is_not_represented_without_receipt` | PASSED |
| `tests/concurrency/test_single_purchase.py::test_simultaneous_claims_yield_exactly_one_execution_claim` | PASSED |
| `tests/concurrency/test_single_purchase.py::test_tie_breaks_deterministically_on_merchant_id` | PASSED |
| `tests/concurrency/test_single_purchase.py::test_two_workers_ticking_concurrently_produce_one_order` | PASSED |
| `tests/protocol/test_demo_scenarios.py::test_demo_a_deceptive_item_price[ap2]` | PASSED |
| `tests/protocol/test_demo_scenarios.py::test_demo_a_deceptive_item_price[vi]` | PASSED |
| `tests/protocol/test_demo_scenarios.py::test_demo_b_valid_drop_purchases_once_with_verifiable_evidence[ap2]` | PASSED |
| `tests/protocol/test_demo_scenarios.py::test_demo_b_valid_drop_purchases_once_with_verifiable_evidence[vi]` | PASSED |
| `tests/protocol/test_demo_scenarios.py::test_demo_c_concurrent_opportunity[ap2]` | PASSED |
| `tests/protocol/test_demo_scenarios.py::test_demo_c_concurrent_opportunity[vi]` | PASSED |
| `tests/protocol/test_demo_scenarios.py::test_demo_d_recovery[ap2]` | PASSED |
| `tests/protocol/test_demo_scenarios.py::test_demo_d_recovery[vi]` | PASSED |
| `tests/protocol/test_profiles.py::test_amount_range_ceiling_is_inclusive_max[ap2]` | PASSED |
| `tests/protocol/test_profiles.py::test_amount_range_ceiling_is_inclusive_max[vi]` | PASSED |
| `tests/protocol/test_profiles.py::test_ap2_unknown_constraint_fails_and_tampered_disclosure_detected` | PASSED |
| `tests/protocol/test_profiles.py::test_ap2_untrusted_provider_key_rejected` | PASSED |
| `tests/protocol/test_profiles.py::test_checkout_jwt_substitution_detected[ap2]` | PASSED |
| `tests/protocol/test_profiles.py::test_checkout_jwt_substitution_detected[vi]` | PASSED |
| `tests/protocol/test_profiles.py::test_expired_authority_is_rejected[ap2]` | PASSED |
| `tests/protocol/test_profiles.py::test_expired_authority_is_rejected[vi]` | PASSED |
| `tests/protocol/test_profiles.py::test_no_second_presentation_without_rejection[ap2]` | PASSED |
| `tests/protocol/test_profiles.py::test_no_second_presentation_without_rejection[vi]` | PASSED |
| `tests/protocol/test_profiles.py::test_profiles_are_not_interchangeable` | PASSED |
| `tests/protocol/test_profiles.py::test_round_trip_merchant_and_payment[ap2]` | PASSED |
| `tests/protocol/test_profiles.py::test_round_trip_merchant_and_payment[vi]` | PASSED |
| `tests/protocol/test_profiles.py::test_vi_structural_rules` | PASSED |
| `tests/protocol/test_profiles.py::test_wrong_merchant_or_item_is_outside_scope[ap2]` | PASSED |
| `tests/protocol/test_profiles.py::test_wrong_merchant_or_item_is_outside_scope[vi]` | PASSED |
| `tests/protocol/test_ucp_merchant.py::test_cancel_and_expiry` | PASSED |
| `tests/protocol/test_ucp_merchant.py::test_checkout_create_verify_and_tamper_detection` | PASSED |
| `tests/protocol/test_ucp_merchant.py::test_complete_requires_idempotency_key_and_authorization` | PASSED |
| `tests/protocol/test_ucp_merchant.py::test_discovery_is_pinned_and_publishes_keys` | PASSED |
| `tests/protocol/test_ucp_merchant.py::test_feed_is_labelled_and_needs_a_destination_for_delivered_price` | PASSED |
| `tests/recovery/test_recovery.py::test_demo_d_dropped_completion_response_recovers_without_buying_twice` | PASSED |
| `tests/recovery/test_recovery.py::test_merchant_5xx_before_processing_is_resolved_by_identical_resubmission` | PASSED |
| `tests/recovery/test_recovery.py::test_merchant_rejection_receipt_allows_ap2_retry_but_consumes_vi` | PASSED |
| `tests/recovery/test_recovery.py::test_restart_before_submission_releases_claim_safely` | PASSED |
| `tests/recovery/test_recovery.py::test_shipping_changes_at_checkout_are_rejected_and_watching_continues` | PASSED |
| `tests/recovery/test_recovery.py::test_stock_disappears_between_feed_and_checkout` | PASSED |
| `tests/recovery/test_recovery.py::test_timeout_keeps_claim_and_blocks_other_merchant_until_reconciled` | PASSED |
| `tests/rules/test_parser.py::test_example_instruction_is_normalised` | PASSED |
| `tests/rules/test_parser.py::test_inclusive_wording_and_named_merchants` | PASSED |
| `tests/rules/test_parser.py::test_missing_pieces_are_reported_not_guessed` | PASSED |
| `tests/rules/test_parser.py::test_similar_products_are_not_authorised` | PASSED |
| `tests/rules/test_price_rules.py::test_99_99_passes_and_100_00_fails_strict_100` | PASSED |
| `tests/rules/test_price_rules.py::test_checkout_validation_boundaries_and_fields` | PASSED |
| `tests/rules/test_price_rules.py::test_draft_rejects_out_of_scope_values` | PASSED |
| `tests/rules/test_price_rules.py::test_merchant_outside_allowlist_rejected` | PASSED |
| `tests/rules/test_price_rules.py::test_missing_shipping_or_tax_prevents_execution` | PASSED |
| `tests/rules/test_price_rules.py::test_money_parsing_is_exact` | PASSED |
| `tests/rules/test_price_rules.py::test_normalize_rule_shows_translation_and_unknowns` | PASSED |
| `tests/rules/test_price_rules.py::test_out_of_stock_and_currency` | PASSED |
| `tests/rules/test_price_rules.py::test_plan_fixtures_105_rejected_98_eligible` | PASSED |
| `tests/rules/test_price_rules.py::test_ranking_cheapest_then_merchant_id` | PASSED |
| `tests/rules/test_price_rules.py::test_sku_mapping_rejects_color_bundle_condition_layout` | PASSED |
| `tests/rules/test_price_rules.py::test_stale_and_expired_quotes_rejected` | PASSED |
| `tests/rules/test_price_rules.py::test_state_machine_edges` | PASSED |
| `tests/rules/test_price_rules.py::test_strict_threshold_translation` | PASSED |
| `tests/rules/test_price_rules.py::test_wrong_variant_and_wrong_sku_rejected` | PASSED |
| `tests/scheduling/test_scheduler.py::test_all_merchants_failing_increases_watch_backoff` | PASSED |
| `tests/scheduling/test_scheduler.py::test_duplicate_scheduler_messages_are_harmless` | PASSED |
| `tests/scheduling/test_scheduler.py::test_expiry_is_decided_by_the_server_clock` | PASSED |
| `tests/scheduling/test_scheduler.py::test_final_check_is_clamped_to_the_deadline` | PASSED |
| `tests/scheduling/test_scheduler.py::test_merchant_errors_back_off_without_blocking_the_other_merchant` | PASSED |
| `tests/scheduling/test_scheduler.py::test_only_one_worker_acquires_the_lease` | PASSED |
| `tests/scheduling/test_scheduler.py::test_pause_resume_cancel_by_user` | PASSED |
| `tests/scheduling/test_scheduler.py::test_stale_worker_lease_cannot_claim_or_submit` | PASSED |
| `tests/scheduling/test_scheduler.py::test_user_update_during_lease_invalidates_worker_version` | PASSED |
| `tests/scheduling/test_scheduler.py::test_watch_is_polled_on_cadence_and_rescheduled_from_now` | PASSED |
