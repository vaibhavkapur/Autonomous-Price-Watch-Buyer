"""Lightweight UCP merchant fixture (business role).

Implements the UCP ``2026-08-25`` business profile at ``/.well-known/ucp`` and
the Checkout capability over REST (create / get / update / complete / cancel),
signs every Checkout as a merchant JWT (required by AP2 and recommended by VI),
verifies AP2 Checkout Mandates or VI L3b credentials at completion, and
"captures" payment through the simulated MPP.

A clearly labelled *application feed* (``/app-feed/offers``) provides delivered
price quotes for polling; it is not a UCP capability.
"""
from .app import MerchantConfig, MerchantState, create_merchant_app  # noqa: F401
