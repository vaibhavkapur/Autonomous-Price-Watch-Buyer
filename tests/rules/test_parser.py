from datetime import datetime, timezone

from watch_domain.parser import parse_request

NOW = datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc)  # Friday
MERCHANTS = {"merchant_a": "Keyboard Depot", "merchant_b": "ClickClack Supply"}


def test_example_instruction_is_normalised():
    out = parse_request(
        "Buy this exact keyboard if its delivered price drops below $100 before Sunday. Use only these two merchants and buy it once.",
        now=NOW, timezone="Asia/Kolkata", known_merchants=MERCHANTS,
    )
    assert out["price_rule"] == {"operator": "lt", "delivered_total_minor": 10000}
    assert out["expires_at"].startswith("2026-09-27T23:59:59")
    assert out["allowed_merchants"] == ["merchant_a", "merchant_b"]
    assert out["max_purchases"] == 1
    assert any("strict comparison" in n for n in out["notes"])
    assert any(u.startswith("product:") for u in out["unknowns"])


def test_inclusive_wording_and_named_merchants():
    out = parse_request("Get the K1 from Keyboard Depot for at most $95.50 by 2026-10-01", now=NOW, known_merchants=MERCHANTS)
    assert out["price_rule"] == {"operator": "lte", "delivered_total_minor": 9550}
    assert out["allowed_merchants"] == ["merchant_a"]
    assert out["expires_at"].startswith("2026-10-01T23:59:59")


def test_missing_pieces_are_reported_not_guessed():
    out = parse_request("buy a keyboard", now=NOW, known_merchants=MERCHANTS)
    assert "price_rule" not in out and "expires_at" not in out
    assert any(u.startswith("price_rule") for u in out["unknowns"])
    assert any(u.startswith("expires_at") for u in out["unknowns"])
    assert any(u.startswith("allowed_merchants") for u in out["unknowns"])


def test_similar_products_are_not_authorised():
    out = parse_request("buy a similar keyboard under $80 within 3 days from ClickClack", now=NOW, known_merchants=MERCHANTS)
    assert any("exact product identity" in n for n in out["notes"])
    assert out["price_rule"]["operator"] == "lt"
