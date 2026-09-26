"""Plan §23 required tests: strict threshold boundaries, missing components,
wrong variant, merchant allowlist. Pure, deterministic, no I/O."""
from datetime import datetime, timedelta, timezone

import pytest

from common.money import inclusive_ceiling, minor_to_display, parse_display_to_minor, satisfies
from offer_evaluation import Offer, PriceComponents, evaluate_offer, rank, validate_checkout
from product_identity import catalog_item, load_merchant_catalog, load_products, verify_mapping
from watch_domain.models import PriceRule, ProductConstraints, WatchDraft, normalize_rule
from watch_domain.states import IllegalTransition, transition

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
FRESH = timedelta(minutes=5)


def offer(total_parts, *, merchant="merchant_a", sku="K1-BLK-US", stock=5, currency="USD", observed=NOW, valid_until=None):
    item, ship, tax = total_parts
    return Offer(merchant, "o1", sku, {}, stock, currency, PriceComponents(item, ship, tax, 0, 0, "test"), observed, valid_until, None, "test")


def evaluate(o, operator="lt", threshold=10000, allowed=("merchant_a", "merchant_b"), now=NOW):
    return evaluate_offer(o, expected_sku="K1-BLK-US", quantity=1, currency="USD", operator=operator, threshold_minor=threshold, allowed_merchants=allowed, now=now, freshness=FRESH)


# ------------------------------------------------------------- money
def test_money_parsing_is_exact():
    assert parse_display_to_minor("$100") == 10000
    assert parse_display_to_minor("99.99") == 9999
    assert parse_display_to_minor("USD 105.00") == 10500
    with pytest.raises(ValueError):
        parse_display_to_minor("1.999")
    assert minor_to_display(9999) == "USD 99.99"


def test_strict_threshold_translation():
    assert inclusive_ceiling("lt", 10000) == 9999
    assert inclusive_ceiling("lte", 10000) == 10000
    assert satisfies("lt", 9999, 10000) and not satisfies("lt", 10000, 10000)


# ------------------------------------------------------ boundaries
def test_99_99_passes_and_100_00_fails_strict_100():
    v = evaluate(offer((8999, 600, 400)))  # 99.99
    assert v.eligibility == "eligible" and v.total_minor == 9999
    v = evaluate(offer((9000, 600, 400)))  # 100.00 exactly
    assert v.eligibility == "rejected" and v.total_minor == 10000
    assert any(r.startswith("price_rule_not_met") for r in v.reasons)


def test_plan_fixtures_105_rejected_98_eligible():
    assert evaluate(offer((8900, 1200, 400))).eligibility == "rejected"  # $105
    assert evaluate(offer((8900, 500, 400))).eligibility == "eligible"  # $98


def test_missing_shipping_or_tax_prevents_execution():
    v = evaluate(offer((8900, None, 400)))
    assert v.eligibility == "incomplete" and "missing_component:shipping" in v.reasons
    v = evaluate(offer((8900, 500, None)))
    assert v.eligibility == "incomplete" and "missing_component:tax" in v.reasons
    assert PriceComponents(8900, 500, None).delivered_total() is None


def test_wrong_variant_and_wrong_sku_rejected():
    v = evaluate(offer((8900, 500, 400), sku="K1-WHT-US"))
    assert v.eligibility == "rejected" and any(r.startswith("sku_mismatch") for r in v.reasons)


def test_merchant_outside_allowlist_rejected():
    v = evaluate(offer((8900, 500, 400), merchant="merchant_c"), allowed=("merchant_a",))
    assert v.eligibility == "rejected" and "merchant_not_allowed:merchant_c" in v.reasons


def test_stale_and_expired_quotes_rejected():
    v = evaluate(offer((8900, 500, 400), observed=NOW - timedelta(minutes=6)))
    assert v.eligibility == "stale"
    v = evaluate(offer((8900, 500, 400), valid_until=NOW))
    assert v.eligibility == "stale"


def test_out_of_stock_and_currency():
    assert evaluate(offer((8900, 500, 400), stock=0)).eligibility == "rejected"
    assert evaluate(offer((8900, 500, 400), currency="EUR")).eligibility == "rejected"


def test_ranking_cheapest_then_merchant_id():
    a = (offer((8900, 500, 400), merchant="merchant_a"), evaluate(offer((8900, 500, 400), merchant="merchant_a")))
    b = (offer((8900, 400, 400), merchant="merchant_b", sku="K1-BLK-US"), evaluate(offer((8900, 400, 400), merchant="merchant_b")))
    assert [o.merchant_id for o, _ in rank([a, b])] == ["merchant_b", "merchant_a"]
    tie = (offer((8900, 500, 400), merchant="merchant_b"), evaluate(offer((8900, 500, 400), merchant="merchant_b")))
    assert [o.merchant_id for o, _ in rank([tie, a])] == ["merchant_a", "merchant_b"]


# --------------------------------------------------- product identity
def test_sku_mapping_rejects_color_bundle_condition_layout():
    products = load_products()
    k1 = products["keyboard_k1_black_us"]
    a = load_merchant_catalog("merchant_a")
    b = load_merchant_catalog("merchant_b")
    assert verify_mapping(k1, catalog_item(a, "K1-BLK-US"))[0]
    assert verify_mapping(k1, catalog_item(b, "KB-K1-US-B"))[0]
    ok, reasons = verify_mapping(k1, catalog_item(a, "K1-WHT-US"))
    assert not ok and any("color" in r for r in reasons)
    ok, reasons = verify_mapping(k1, catalog_item(a, "K1-BLK-US-BUNDLE"))
    assert not ok and "bundle_mismatch" in reasons
    ok, reasons = verify_mapping(k1, catalog_item(b, "KB-K1-US-B-REFURB"))
    assert not ok and any("condition" in r for r in reasons)
    ok, reasons = verify_mapping(k1, catalog_item(b, "KB-K1-UK-B"))
    assert not ok and any("layout" in r for r in reasons)
    assert verify_mapping(k1, None) == (False, ["sku_not_in_merchant_catalog"])


# -------------------------------------------------------- checkout
def checkout(total_parts, status="ready_for_complete", sku="K1-BLK-US", qty=1, merchant="merchant_a", extra_line=False, declared_total=None):
    item, ship, tax = total_parts
    totals = [{"type": "subtotal", "amount": item}, {"type": "fulfillment", "amount": ship}]
    if tax is not None:
        totals.append({"type": "tax", "amount": tax})
        totals.append({"type": "total", "amount": declared_total if declared_total is not None else item + ship + tax})
    lines = [{"id": "li_1", "item": {"id": sku, "title": "K1", "price": item}, "quantity": qty}]
    if extra_line:
        lines.append({"id": "li_2", "item": {"id": "K1-WHT-US", "title": "K1 white", "price": 100}, "quantity": 1})
    return {"id": "chk", "status": status, "currency": "USD", "merchant": {"id": merchant}, "line_items": lines, "totals": totals, "expires_at": "2026-09-26T18:00:00Z"}


def validate(c, **kw):
    args = dict(merchant_id="merchant_a", expected_sku="K1-BLK-US", quantity=1, currency="USD", operator="lt", threshold_minor=10000, now=NOW)
    args.update(kw)
    return validate_checkout(c, **args)


def test_checkout_validation_boundaries_and_fields():
    assert validate(checkout((8999, 600, 400))).eligibility == "eligible"
    assert validate(checkout((9000, 600, 400))).eligibility == "rejected"
    assert validate(checkout((8900, 500, None))).eligibility == "incomplete"
    assert validate(checkout((8900, 500, 400), status="incomplete")).eligibility == "rejected"
    assert validate(checkout((8900, 500, 400), sku="K1-WHT-US")).eligibility == "rejected"
    assert validate(checkout((8900, 500, 400), qty=2)).eligibility == "rejected"
    assert validate(checkout((8900, 500, 400), extra_line=True)).eligibility == "rejected"
    assert validate(checkout((8900, 500, 400), merchant="merchant_b")).eligibility == "rejected"
    v = validate(checkout((8900, 500, 400), declared_total=9700))
    assert v.eligibility == "rejected" and any(r.startswith("total_mismatch") for r in v.reasons)
    assert validate(checkout((8900, 500, 400)), now=datetime(2026, 9, 26, 19, 0, tzinfo=timezone.utc)).eligibility == "rejected"


# ------------------------------------------------ rule normalisation
def test_normalize_rule_shows_translation_and_unknowns():
    draft = WatchDraft(
        product=ProductConstraints(product_id="keyboard_k1_black_us", title="K1", attributes={"color": "black"}),
        merchant_skus={"merchant_a": "K1-BLK-US"}, price_rule=PriceRule(operator="lt", delivered_total_minor=10000),
        allowed_merchants=["merchant_a"], destination_id=None, expires_at=NOW + timedelta(days=1),
    )
    ex = normalize_rule(draft, {"merchant_a": "Keyboard Depot"})
    assert ex.inclusive_ceiling_minor == 9999
    assert any("USD 99.99" in s for s in ex.sentences)
    assert any("strictly below USD 100.00" in s for s in ex.sentences)
    assert ex.unknowns and "destination_id" in ex.unknowns[0]


def test_draft_rejects_out_of_scope_values():
    with pytest.raises(ValueError):
        WatchDraft(product=ProductConstraints(product_id="p", title="t"), merchant_skus={}, price_rule=PriceRule(operator="lt", delivered_total_minor=10000), allowed_merchants=["m"], expires_at=NOW, quantity=2)
    with pytest.raises(ValueError):
        WatchDraft(product=ProductConstraints(product_id="p", title="t"), merchant_skus={}, price_rule=PriceRule(operator="lt", delivered_total_minor=10000), allowed_merchants=["m"], expires_at=NOW, currency="EUR")
    with pytest.raises(ValueError):
        WatchDraft(product=ProductConstraints(product_id="p", title="t"), merchant_skus={}, price_rule=PriceRule(operator="lt", delivered_total_minor=10000), allowed_merchants=[], expires_at=NOW)


# ------------------------------------------------------ state machine
def test_state_machine_edges():
    assert transition("watching", "candidate_found") == "candidate_found"
    assert transition("checkout_validating", "watching") == "watching"
    assert transition("submitting", "reconciliation_required") == "reconciliation_required"
    assert transition("reconciliation_required", "resolved_not_purchased") == "resolved_not_purchased"
    with pytest.raises(IllegalTransition):
        transition("purchased", "watching")
    with pytest.raises(IllegalTransition):
        transition("watching", "submitting")
    with pytest.raises(IllegalTransition):
        transition("resolved_not_purchased", "watching")
