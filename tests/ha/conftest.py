"""Home Assistant test setup. Needs: pip install pytest-homeassistant-custom-component"""

from unittest.mock import patch

import httpx
import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from tests.fake_unifi import FakeUnifi  # noqa: E402



@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def no_blocking_calls_in_event_loop():
    """Fail any test where our code blocks Home Assistant's event loop (file I/O, sleep, sockets...).

    Same tool Home Assistant core uses in its own test suite.
    """
    blockbuster = pytest.importorskip("blockbuster")
    with blockbuster.blockbuster_ctx(["custom_components.family_wifi"]) as bb:
        yield bb


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
