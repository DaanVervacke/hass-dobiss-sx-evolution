"""Shared pytest fixtures for the dobiss_sx_evolution test suite.

Uses pytest-homeassistant-custom-component (PHACC) so tests can spin up a real
HomeAssistant instance and load this custom integration from ``custom_components/``.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# PHACC plugin must be declared in the ROOT conftest (this one).
pytest_plugins = ["pytest_homeassistant_custom_component"]


# Connection details used by config-flow / setup tests.
MOCK_CONFIG = {
    "host": "192.168.1.10",
    "port": 29536,
    "interface": "can0",
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading of the custom integration in every test.

    The ``enable_custom_integrations`` fixture is provided by PHACC.
    """
    yield


@pytest.fixture
def mock_probe():
    """Patch the config-flow CAN probe to a no-op success."""
    with patch(
        "custom_components.dobiss_sx_evolution.config_flow._probe_bus_sync",
        return_value=None,
    ) as probe:
        yield probe


@pytest.fixture
def mock_controller():
    """Patch DobissController used by the coordinator with a fake.

    Exposes the attributes/methods the integration touches during setup,
    teardown, and listener wiring.
    """
    fake = MagicMock(name="DobissController")
    fake.host = MOCK_CONFIG["host"]
    fake.port = MOCK_CONFIG["port"]
    fake.interface = MOCK_CONFIG["interface"]
    fake.modules = []
    fake.lights = []
    fake.dimmers = []
    fake.shutters = []
    fake.states = {}
    fake.reconnect_count = 0
    fake._bus = object()  # truthy so coordinator doesn't raise UpdateFailed

    fake.async_setup = AsyncMock(return_value=None)
    fake.async_shutdown = AsyncMock(return_value=None)
    fake.async_request_dump = AsyncMock(return_value=None)

    # Listener registration returns an unsubscribe callable.
    unsubscribe = MagicMock(name="unsubscribe")

    def _add_listener(cb):
        return unsubscribe

    fake.async_add_listener = MagicMock(side_effect=_add_listener)
    fake.add_listener = MagicMock(side_effect=_add_listener)
    fake.remove_listener = MagicMock()
    fake.request_state_dump = MagicMock(return_value=None)

    with patch(
        "custom_components.dobiss_sx_evolution.coordinator.DobissController",
        return_value=fake,
    ):
        yield fake
