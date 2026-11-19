"""Test setup: let Home Assistant load the integration from this repository."""

import pytest


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations):
    """Allow the kramer custom integration to load."""
    yield


@pytest.fixture(autouse=True)
def _allow_local_sockets(socket_enabled):
    """Point the tests at a simulated unit on localhost."""
    yield
