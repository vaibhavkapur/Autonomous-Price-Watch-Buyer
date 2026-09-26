"""UCP platform-side adapter (Shopping Agent → Business).

Pinned to UCP release ``2026-08-25``: discovery via ``/.well-known/ucp``,
Checkout capability over REST, ``UCP-Agent`` header, ``Idempotency-Key`` on
Complete Checkout, AP2 mandate handler negotiation. Also reads the merchant's
clearly labelled application offer feed for polling.
"""
from .client import (  # noqa: F401
    MerchantEndpoint,
    MerchantError,
    MerchantTimeout,
    UCPClient,
    UCP_VERSION,
)
