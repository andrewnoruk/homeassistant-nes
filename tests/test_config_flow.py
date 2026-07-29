"""Tests for the NES config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.nes.api import NESAuthError, NESConnectionError
from custom_components.nes.const import DOMAIN

# The pytest-homeassistant-custom-component plugin provides hass fixture
# but we need to patch the integration loader to find our custom component.
MOCK_PATCH_TARGET = "custom_components.nes.config_flow.NESApiClient"


@pytest.fixture(autouse=True)
async def _register_integration(hass: HomeAssistant) -> None:
    """Register the NES integration with HA loader."""
    from homeassistant.loader import Integration

    integration = Integration(
        hass,
        "custom_components.nes",
        None,
        {
            "domain": DOMAIN,
            "name": "Nashville Electric Service",
            "config_flow": True,
            "documentation": "https://github.com/maxbeizer/homeassistant-nes",
            "codeowners": ["@maxbeizer"],
            "iot_class": "cloud_polling",
            "version": "0.1.0",
            "requirements": [],
            "dependencies": [],
            "integration_type": "service",
        },
    )
    hass.data.setdefault("integrations", {})[DOMAIN] = integration


async def test_user_flow_success(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    mock_nes_client: MagicMock,
) -> None:
    """Test successful user config flow."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"username": "test@example.com", "password": "testpassword"},
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "NES (7013678056)"
    assert result["data"] == {
        "username": "test@example.com",
        "password": "testpassword",
    }
    assert result["result"].unique_id == "105112"


async def test_user_flow_invalid_auth(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    mock_nes_client: MagicMock,
) -> None:
    """Test config flow with invalid credentials."""
    mock_nes_client.async_authenticate.side_effect = NESAuthError("Bad creds")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"username": "bad@example.com", "password": "wrong"},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_flow_cannot_connect(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    mock_nes_client: MagicMock,
) -> None:
    """Test config flow when NES is unreachable."""
    mock_nes_client.async_authenticate.side_effect = NESConnectionError("Timeout")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"username": "test@example.com", "password": "testpassword"},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_unknown_error(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    mock_nes_client: MagicMock,
) -> None:
    """Test config flow with unexpected error."""
    mock_nes_client.async_authenticate.side_effect = RuntimeError("Boom")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"username": "test@example.com", "password": "testpassword"},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "unknown"}


async def test_user_flow_duplicate_account(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    mock_nes_client: MagicMock,
) -> None:
    """Test config flow aborts on duplicate account."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    # Create an existing entry with the same unique_id
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="NES (7013678056)",
        data={"username": "existing@example.com", "password": "pass"},
        unique_id="105112",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"username": "test@example.com", "password": "testpassword"},
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_user_flow_auth_fails_on_customer_fetch(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    mock_nes_client: MagicMock,
) -> None:
    """Test config flow when auth succeeds but customer fetch fails."""
    mock_nes_client.async_get_customer.side_effect = NESAuthError("Token expired")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"username": "test@example.com", "password": "testpassword"},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
