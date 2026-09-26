"""Exact product identity (plan §9).

Identity is established from *configured merchant catalog data* and the SKU
mapping fixed at watch creation. Free-text similarity is never sufficient.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common.config import ROOT

CATALOG_DIR = ROOT / "fixtures" / "catalogs"

# Attributes that must match exactly for a SKU to count as the same product.
IDENTITY_ATTRIBUTES = ("layout", "color", "condition", "connectivity")


def load_products() -> Dict[str, Dict[str, Any]]:
    data = json.loads((CATALOG_DIR / "products.json").read_text())
    return {p["product_id"]: p for p in data["products"]}


def load_merchant_catalog(merchant_id: str, path: Optional[Path] = None) -> Dict[str, Any]:
    p = path or (CATALOG_DIR / ("%s.json" % merchant_id))
    return json.loads(p.read_text())


def catalog_item(catalog: Dict[str, Any], sku: str) -> Optional[Dict[str, Any]]:
    for item in catalog["items"]:
        if item["sku"] == sku:
            return item
    return None


def verify_mapping(product: Dict[str, Any], item: Optional[Dict[str, Any]]) -> Tuple[bool, List[str]]:
    """Check a merchant catalog item against the canonical product identity."""
    reasons: List[str] = []
    if item is None:
        return False, ["sku_not_in_merchant_catalog"]
    if item.get("canonical_product_id") != product["product_id"]:
        reasons.append("canonical_product_mismatch:%s" % item.get("canonical_product_id"))
    want = product.get("attributes", {})
    have = item.get("attributes", {})
    for attr in IDENTITY_ATTRIBUTES:
        if attr in want and have.get(attr) != want[attr]:
            reasons.append("attribute_mismatch:%s expected=%s actual=%s" % (attr, want[attr], have.get(attr)))
    if bool(item.get("bundle", False)) != bool(product.get("bundle", False)):
        reasons.append("bundle_mismatch")
    if item.get("quantity_per_unit", 1) != 1:
        reasons.append("multipack_not_authorised")
    return (not reasons), reasons


def match_line_items(
    line_items: List[Dict[str, Any]], expected_sku: str, expected_quantity: int
) -> Tuple[bool, List[str]]:
    """Validate a UCP checkout's line items against the watch: exactly one line,
    exact SKU, exact quantity, nothing else in the cart."""
    reasons: List[str] = []
    if len(line_items) != 1:
        reasons.append("unexpected_line_item_count:%d" % len(line_items))
        return False, reasons
    li = line_items[0]
    item_id = (li.get("item") or {}).get("id")
    if item_id != expected_sku:
        reasons.append("sku_mismatch expected=%s actual=%s" % (expected_sku, item_id))
    if int(li.get("quantity", 0)) != expected_quantity:
        reasons.append("quantity_mismatch expected=%d actual=%s" % (expected_quantity, li.get("quantity")))
    unit = (li.get("item") or {}).get("quantity_unit")
    if unit and unit.get("unit") not in (None, "C62"):
        reasons.append("non_each_sale_basis:%s" % unit.get("unit"))
    return (not reasons), reasons
