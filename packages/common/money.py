"""Exact integer money helpers. All amounts are ISO 4217 minor units (USD cents)."""
from __future__ import annotations

from typing import Optional

SUPPORTED_CURRENCIES = {"USD": 2}


def minor_to_display(amount: int, currency: str = "USD") -> str:
    scale = SUPPORTED_CURRENCIES.get(currency, 2)
    sign = "-" if amount < 0 else ""
    amount = abs(amount)
    whole, frac = divmod(amount, 10 ** scale)
    return "%s%s %d.%0*d" % (sign, currency, whole, scale, frac)


def parse_display_to_minor(text: str, currency: str = "USD") -> int:
    """Parse '$100', '99.99', 'USD 105.00' into minor units, exactly."""
    scale = SUPPORTED_CURRENCIES.get(currency, 2)
    cleaned = text.strip().replace(currency, "").replace("$", "").replace(",", "").strip()
    if not cleaned:
        raise ValueError("empty amount")
    negative = cleaned.startswith("-")
    if negative:
        cleaned = cleaned[1:]
    if "." in cleaned:
        whole, frac = cleaned.split(".", 1)
        if len(frac) > scale:
            raise ValueError("too many decimal places for %s: %r" % (currency, text))
        frac = frac.ljust(scale, "0")
    else:
        whole, frac = cleaned, "0" * scale
    if not whole:
        whole = "0"
    if not (whole.isdigit() and frac.isdigit()):
        raise ValueError("not a monetary amount: %r" % text)
    value = int(whole) * 10 ** scale + int(frac)
    return -value if negative else value


def inclusive_ceiling(operator: str, threshold_minor: int) -> Optional[int]:
    """Translate a comparison rule into the largest allowed integer total.

    'lt'  -> threshold - 1   (strictly below)
    'lte' -> threshold       (at most)
    Returns None for operators that do not define an upper bound.
    """
    if operator == "lt":
        return threshold_minor - 1
    if operator == "lte":
        return threshold_minor
    return None


def satisfies(operator: str, total_minor: int, threshold_minor: int) -> bool:
    if operator == "lt":
        return total_minor < threshold_minor
    if operator == "lte":
        return total_minor <= threshold_minor
    raise ValueError("unsupported price operator: %r" % operator)
