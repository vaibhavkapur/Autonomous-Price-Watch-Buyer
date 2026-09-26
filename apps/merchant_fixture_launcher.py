"""Shared launcher for the two merchant fixture processes."""
from __future__ import annotations

import logging
import os

import uvicorn

from authorization_profiles.keys import KeyRing
from common.config import load_settings
from merchant_fixture import MerchantConfig, create_merchant_app
from product_identity import load_merchant_catalog


def run(merchant_id: str, default_port: int) -> None:  # pragma: no cover - process entry point
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    settings = load_settings()
    keys = KeyRing.load_or_create(settings.keys_path)
    catalog = load_merchant_catalog(merchant_id)
    port = int(os.environ.get("PORT", str(default_port)))
    base_url = os.environ.get("MERCHANT_BASE_URL", "http://localhost:%d" % port)
    # The merchant holds only its own private key plus the public trust store.
    cfg = MerchantConfig(merchant_id, catalog["name"], catalog["website"], catalog, keys.private(merchant_id), keys.trust_store(), base_url=base_url)
    uvicorn.run(create_merchant_app(cfg), host="0.0.0.0", port=port)
