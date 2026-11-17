"""Shared test setup: allow the localhost sockets the simulated units use."""

import pytest


@pytest.fixture(autouse=True)
def _allow_local_sockets(socket_enabled):
    """Point the tests at a simulated unit on localhost."""
    yield
