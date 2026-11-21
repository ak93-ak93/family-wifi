"""Home Assistant test setup. Needs: pip install pytest-homeassistant-custom-component"""

from unittest.mock import patch

import httpx
import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from tests.fake_unifi import FakeUnifi  # noqa: E402

pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def fake_unifi():
    """Route every UniFi HTTP call made by the integration to a FakeUnifi."""
    from custom_components.family_wifi.core import unifi

    fake = FakeUnifi()
    original = unifi.UnifiClient.__init__

    def init(self, cfg, transport=None):
        original(self, cfg, transport=httpx.MockTransport(fake))

    with patch.object(unifi.UnifiClient, "__init__", init):
        yield fake
