"""Application watch model and rule normalisation (plan §7, §8)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from common.money import inclusive_ceiling, minor_to_display


class ProductConstraints(BaseModel):
    """Exact product identity. Attributes are machine-checked against merchant
    catalog data, never against free-text descriptions."""

    product_id: str
    title: str
    attributes: Dict[str, str] = Field(default_factory=dict)  # e.g. layout=US, color=black, condition=new
    bundle: bool = False


class PriceRule(BaseModel):
    operator: Literal["lt", "lte"]
    delivered_total_minor: int

    @field_validator("delivered_total_minor")
    @classmethod
    def _positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("threshold must be positive")
        return v


class WatchDraft(BaseModel):
    original_request: Optional[str] = None
    product: ProductConstraints
    merchant_skus: Dict[str, str]
    quantity: int = 1
    currency: str = "USD"
    price_rule: PriceRule
    allowed_merchants: List[str]
    destination_id: Optional[str] = None
    expires_at: datetime
    timezone: str = "UTC"
    max_purchases: int = 1
    authorization_profile: Literal["ap2", "vi"] = "ap2"
    poll_interval_seconds: int = 60

    @field_validator("currency")
    @classmethod
    def _usd_only(cls, v: str) -> str:
        if v != "USD":
            raise ValueError("MVP supports USD only")
        return v

    @field_validator("quantity", "max_purchases")
    @classmethod
    def _one(cls, v: int) -> int:
        if v != 1:
            raise ValueError("MVP supports exactly one unit and one purchase")
        return v

    @field_validator("allowed_merchants")
    @classmethod
    def _non_empty(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError("at least one allowed merchant is required")
        if len(set(v)) != len(v):
            raise ValueError("duplicate merchants in allowlist")
        return v

    def missing_for_activation(self) -> List[str]:
        missing = []
        if not self.destination_id:
            missing.append("destination_id: a shipping address is required to compute tax and shipping")
        for m in self.allowed_merchants:
            if m not in self.merchant_skus:
                missing.append("merchant_skus[%s]: no SKU mapping for allowed merchant" % m)
        return missing


class RuleExplanation(BaseModel):
    """What the user will sign, rendered as deterministic sentences."""

    executable_rule: Dict[str, Any]
    inclusive_ceiling_minor: int
    sentences: List[str]
    unknowns: List[str]


def normalize_rule(draft: WatchDraft, merchant_names: Optional[Dict[str, str]] = None) -> RuleExplanation:
    rule = draft.price_rule
    ceiling = inclusive_ceiling(rule.operator, rule.delivered_total_minor)
    assert ceiling is not None
    op_word = "strictly below" if rule.operator == "lt" else "at most"
    names = merchant_names or {}
    merchants = ", ".join("%s (%s)" % (names.get(m, m), draft.merchant_skus.get(m, "?")) for m in draft.allowed_merchants)
    attrs = ", ".join("%s=%s" % (k, v) for k, v in sorted(draft.product.attributes.items())) or "no variant attributes"
    sentences = [
        "Product: exactly '%s' [%s] (%s). Substitutes, bundles, other colours/layouts or used items are not authorised."
        % (draft.product.title, draft.product.product_id, attrs),
        "Quantity: %d unit. Maximum successful purchases: %d." % (draft.quantity, draft.max_purchases),
        "Price rule: delivered total (item + shipping + tax + mandatory fees − confirmed discounts) must be %s %s."
        % (op_word, minor_to_display(rule.delivered_total_minor, draft.currency)),
        "Translated ceiling: the largest qualifying delivered total is %s (integer cents; the protocol constraint uses an inclusive maximum of %d)."
        % (minor_to_display(ceiling, draft.currency), ceiling),
        "Merchants: only %s." % merchants,
        "Deadline: authority ends at %s (%s). Nothing may be submitted after this instant, even if scheduled before it."
        % (draft.expires_at.isoformat(), draft.timezone),
        "Authorization profile: %s." % ("AP2 v0.2 (autonomous mandates)" if draft.authorization_profile == "ap2" else "Verifiable Intent v0.1-draft (autonomous, 3-layer)"),
        "Destination: %s." % (draft.destination_id or "MISSING — the watch cannot become purchase-ready until an address is provided"),
    ]
    return RuleExplanation(
        executable_rule={
            "operator": rule.operator,
            "delivered_total_minor": rule.delivered_total_minor,
            "inclusive_ceiling_minor": ceiling,
            "currency": draft.currency,
        },
        inclusive_ceiling_minor=ceiling,
        sentences=sentences,
        unknowns=draft.missing_for_activation(),
    )
