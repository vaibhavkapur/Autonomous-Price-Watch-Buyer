"""Authorization profiles.

Two independent, versioned profiles implement the same shopping scenario:

* ``ap2``  — AP2 v0.2 autonomous ("Human Not Present") mandates
* ``vi``   — Verifiable Intent v0.1-draft autonomous 3-layer credentials

Both build on the shared SD-JWT primitives in :mod:`authorization_profiles.sdjwt`
but use their own claim vocabularies, structural rules, and test fixtures.
Artifacts are never relabelled from one profile to the other.
"""
from .base import AuthorizationProfile, ConsentRequest, PreparedPresentation, VerificationResult, ProfileError  # noqa: F401
from .registry import get_profile, PROFILE_VERSIONS  # noqa: F401
