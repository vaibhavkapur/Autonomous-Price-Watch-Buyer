from __future__ import annotations

from typing import Dict

from .ap2 import AP2Profile, VERSION as AP2_VERSION
from .base import AuthorizationProfile
from .keys import KeyRing, TrustStore
from .vi import VIProfile, VERSION as VI_VERSION

PROFILE_VERSIONS: Dict[str, str] = {"ap2": AP2_VERSION, "vi": VI_VERSION}


def get_profile(name: str, keys: KeyRing, trust: TrustStore) -> AuthorizationProfile:
    if name == "ap2":
        return AP2Profile(keys, trust)
    if name == "vi":
        return VIProfile(keys, trust)
    raise ValueError("unknown authorization profile %r" % name)
