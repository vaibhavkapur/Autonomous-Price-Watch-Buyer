import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in ("packages", "apps", "tests"):
    sp = str(ROOT / p)
    if sp not in sys.path:
        sys.path.insert(0, sp)

from harness import Harness  # noqa: E402


@pytest.fixture
def harness():
    h = Harness()
    try:
        yield h
    finally:
        h.close()
