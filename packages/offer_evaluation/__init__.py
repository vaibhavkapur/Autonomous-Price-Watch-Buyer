"""Delivered-price calculation and eligibility (plan §9, §10, §12 steps 3-6)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from common.clock import parse_iso
from common.money import satisfies
from product_identity import match_line_items

REQUIRED_COMPONENTS = ("item_subtotal", "shipping", "tax")
OPTIONAL_COMPONENTS = ("fees", "discount")


@dataclass
class PriceComponents:
    """Every component carries its source and calculation timestamp."""

    item_subtotal: Optional[int]
    shipping: Optional[int]
    tax: Optional[int]
    fees: Optional[int] = 0
    discount: Optional[int] = 0
    source: str = ""
    calculated_at: Optional[str] = None

    def missing(self) -> List[str]:
        return [c for c in REQUIRED_COMPONENTS if getattr(self, c) is None]

    def delivered_total(self) -> Optional[int]:
        """delivered total = item subtotal + tax + shipping + mandatory fees − valid discounts"""
        if self.missing():
            return None
        return int(self.item_subtotal) + int(self.tax) + int(self.shipping) + int(self.fees or 0) - int(self.discount or 0)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "item_subtotal": self.item_subtotal,
            "shipping": self.shipping,
            "tax": self.tax,
            "fees": self.fees,
            "discount": self.discount,
            "source": self.source,
            "calculated_at": self.calculated_at,
            "delivered_total": self.delivered_total(),
        }


@dataclass
class Offer:
    merchant_id: str
    native_offer_id: Optional[str]
    sku: Optional[str]
    variant: Dict[str, Any]
    quantity_available: Optional[int]
    currency: Optional[str]
    components: PriceComponents
    observed_at: datetime
    valid_until: Optional[datetime]
    delivery_estimate: Optional[str]
    source_reference: str
    error: Optional[str] = None


@dataclass
class Verdict:
    eligibility: str  # eligible | rejected | incomplete | stale | error
    reasons: List[str] = field(default_factory=list)
    total_minor: Optional[int] = None


def offer_from_feed(merchant_id: str, payload: Dict[str, Any], observed_at: datetime, source_reference: str) -> Offer:
    comp = PriceComponents(
        item_subtotal=payload.get("item_price_minor"),
        shipping=payload.get("shipping_minor"),
        tax=payload.get("tax_minor"),
        fees=payload.get("fees_minor", 0),
        discount=0,  # feed discounts are never trusted; only a final checkout confirms a discount
        source="application_feed",
        calculated_at=payload.get("quoted_at"),
    )
    if comp.item_subtotal is not None and payload.get("quantity"):
        comp.item_subtotal = int(comp.item_subtotal) * int(payload["quantity"])
    return Offer(
        merchant_id=merchant_id,
        native_offer_id=payload.get("offer_id"),
        sku=payload.get("sku"),
        variant=payload.get("attributes") or {},
        quantity_available=payload.get("quantity_available"),
        currency=payload.get("currency"),
        components=comp,
        observed_at=observed_at,
        valid_until=parse_iso(payload["valid_until"]) if payload.get("valid_until") else None,
        delivery_estimate=payload.get("delivery_estimate"),
        source_reference=source_reference,
    )


def evaluate_offer(
    offer: Offer,
    *,
    expected_sku: str,
    quantity: int,
    currency: str,
    operator: str,
    threshold_minor: int,
    allowed_merchants: Sequence[str],
    now: datetime,
    freshness: timedelta,
) -> Verdict:
    """Deterministic eligibility. Every rejection carries machine-readable reasons."""
    reasons: List[str] = []
    if offer.error:
        return Verdict("error", ["collector_error:%s" % offer.error])
    if offer.merchant_id not in allowed_merchants:
        reasons.append("merchant_not_allowed:%s" % offer.merchant_id)
    if offer.sku != expected_sku:
        reasons.append("sku_mismatch expected=%s actual=%s" % (expected_sku, offer.sku))
    if offer.currency != currency:
        reasons.append("currency_mismatch expected=%s actual=%s" % (currency, offer.currency))
    if offer.quantity_available is None or offer.quantity_available < quantity:
        reasons.append("insufficient_stock available=%s wanted=%d" % (offer.quantity_available, quantity))
    if reasons:
        return Verdict("rejected", reasons, offer.components.delivered_total())

    if now - offer.observed_at > freshness:
        return Verdict("stale", ["observation_older_than_%ds" % int(freshness.total_seconds())], offer.components.delivered_total())
    if offer.valid_until is not None and offer.valid_until <= now:
        return Verdict("stale", ["quote_expired_at:%s" % offer.valid_until.isoformat()], offer.components.delivered_total())

    missing = offer.components.missing()
    if missing:
        return Verdict("incomplete", ["missing_component:%s" % c for c in missing])
    total = offer.components.delivered_total()
    assert total is not None
    if not satisfies(operator, total, threshold_minor):
        return Verdict(
            "rejected",
            ["price_rule_not_met total=%d operator=%s threshold=%d" % (total, operator, threshold_minor)],
            total,
        )
    return Verdict("eligible", ["price_rule_met total=%d operator=%s threshold=%d" % (total, operator, threshold_minor)], total)


def rank(candidates: List[Tuple[Offer, Verdict]]) -> List[Tuple[Offer, Verdict]]:
    """Cheapest delivered total first; deterministic tie-breaker on merchant id then offer id."""
    return sorted(
        candidates,
        key=lambda ov: (ov[1].total_minor if ov[1].total_minor is not None else 1 << 62, ov[0].merchant_id, ov[0].native_offer_id or ""),
    )


# ------------------------------------------------------------------ checkout
def totals_to_components(totals: List[Dict[str, Any]], source: str, calculated_at: str) -> PriceComponents:
    """Map UCP ``totals[]`` entries (subtotal, fulfillment, tax, fee, discount, total)."""
    by_type: Dict[str, int] = {}
    for t in totals:
        by_type[t["type"]] = by_type.get(t["type"], 0) + int(t["amount"])
    return PriceComponents(
        item_subtotal=by_type.get("subtotal"),
        shipping=by_type.get("fulfillment"),
        tax=by_type.get("tax"),
        fees=by_type.get("fee", 0),
        discount=by_type.get("discount", 0),
        source=source,
        calculated_at=calculated_at,
    )


def validate_checkout(
    checkout: Dict[str, Any],
    *,
    merchant_id: str,
    expected_sku: str,
    quantity: int,
    currency: str,
    operator: str,
    threshold_minor: int,
    now: datetime,
) -> Verdict:
    """Check every final checkout field against the watch (plan §12 step 6)."""
    reasons: List[str] = []
    if checkout.get("status") != "ready_for_complete":
        reasons.append("checkout_status:%s" % checkout.get("status"))
        for m in checkout.get("messages", []) or []:
            if m.get("type") == "error":
                reasons.append("checkout_message:%s" % m.get("code"))
    if checkout.get("currency") != currency:
        reasons.append("currency_mismatch expected=%s actual=%s" % (currency, checkout.get("currency")))
    merchant = checkout.get("merchant") or {}
    if merchant.get("id") != merchant_id:
        reasons.append("merchant_mismatch expected=%s actual=%s" % (merchant_id, merchant.get("id")))
    ok, li_reasons = match_line_items(checkout.get("line_items") or [], expected_sku, quantity)
    reasons.extend(li_reasons)
    exp = checkout.get("expires_at")
    if exp:
        try:
            if parse_iso(exp) <= now:
                reasons.append("checkout_expired:%s" % exp)
        except ValueError:
            reasons.append("checkout_expires_at_unparseable")

    totals = checkout.get("totals") or []
    comp = totals_to_components(totals, "ucp_checkout:%s" % checkout.get("id"), now.isoformat())
    declared_total = next((int(t["amount"]) for t in totals if t["type"] == "total"), None)
    if declared_total is None:
        reasons.append("missing_component:total")
    missing = comp.missing()
    for c in missing:
        reasons.append("missing_component:%s" % c)
    computed = comp.delivered_total()
    if computed is not None and declared_total is not None and computed != declared_total:
        reasons.append("total_mismatch declared=%d computed=%d" % (declared_total, computed))
    if reasons:
        return Verdict("rejected" if not missing else "incomplete", reasons, declared_total)
    assert declared_total is not None
    if not satisfies(operator, declared_total, threshold_minor):
        return Verdict("rejected", ["price_rule_not_met total=%d operator=%s threshold=%d" % (declared_total, operator, threshold_minor)], declared_total)
    return Verdict("eligible", ["price_rule_met total=%d operator=%s threshold=%d" % (declared_total, operator, threshold_minor)], declared_total)
