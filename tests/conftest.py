"""Common test fixtures."""
import pytest
from pytest_homeassistant_custom_component.plugins import enable_custom_integrations

pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable the custom integration for every test."""
    yield