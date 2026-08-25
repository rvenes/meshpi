from __future__ import annotations

import pytest

from meshpi.i18n import set_language


@pytest.fixture(autouse=True)
def _default_legacy_test_language():
    """Existing tests model pre-i18n users, whose compatible default is Nynorsk."""
    set_language("nn")
    yield
    set_language("nn")
