"""Tests for the NES config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nes.api import (
    NESAuthError,
    NESConnectionError,
    NESServiceLocation,
)
from custom_components.nes.const import DOMAIN


@pytest.fixture(autouse=True)
async def _register_integration(hass: HomeAssistant) -> None:
    """Register the NES integration with the Home Assistant loader."""
    from homeassistant.loader import Integration

    integration = Integration(
        hass,
        "custom_components.nes",
        None,
        {
            "domain": DOMAIN,
            "name": "Nashville Electric Service",
            "config_flow": True,
            "documentation": "https://github.com/andrewnoruk/homeassistant-nes",
            "codeowners": ["@andrewnoruk"],
            "iot_class": "cloud_polling",
            "version": "0.2.0",
            "requirements": [],
            "dependencies": [],
            "integration_type": "service",
        },
    )
    hass.data.setdefault("integrations", {})[DOMAIN] = integration


async def _start_user_flow(hass: HomeAssistant) -> config_entries.ConfigFlowResult:
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def _submit_credentials(
    hass: HomeAssistant, result: config_entries.ConfigFlowResult
) -> config_entries.ConfigFlowResult:
    return await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"username": "test@example.com", "password": "testpassword"},
    )


async def test_user_flow_single_location_creates_entry(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    mock_nes_client: MagicMock,
) -> None:
    """A login with one service is configured without another prompt."""
    result = await _start_user_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await _submit_credentials(hass, result)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "NES (123 Main St, Nashville, TN 37201)"
    assert result["data"] == {
        "username": "test@example.com",
        "password": "testpassword",
        "account_number": "7013678056",
        "service_id": "service-1",
        "service_type": "Electric",
        "service_address": "123 Main St, Nashville, TN 37201",
    }
    assert result["result"].unique_id == "7013678056:service-1"


async def test_user_flow_multiple_locations_prompts_for_address(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    mock_nes_client: MagicMock,
) -> None:
    """Multiple linked locations are presented for explicit selection."""
    mock_nes_client.async_get_service_locations.return_value = [
        NESServiceLocation("7013678056", "service-1", "Electric", "123 Main St"),
        NESServiceLocation("7013672147", "service-2", "Electric", "456 Oak Ave"),
    ]

    result = await _submit_credentials(hass, await _start_user_flow(hass))

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "account"
    assert result["data_schema"]({"service_location": "7013672147:service-2"}) == {
        "service_location": "7013672147:service-2"
    }

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"service_location": "7013672147:service-2"}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "NES (456 Oak Ave)"
    assert result["data"]["account_number"] == "7013672147"
    assert result["data"]["service_id"] == "service-2"
    assert result["result"].unique_id == "7013672147:service-2"


async def test_same_login_can_configure_a_different_service(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    mock_nes_client: MagicMock,
) -> None:
    """The unique ID is the service, not the shared portal login."""
    MockConfigEntry(
        domain=DOMAIN,
        title="NES (123 Main St)",
        data={"username": "test@example.com", "password": "pass"},
        unique_id="7013678056:service-1",
    ).add_to_hass(hass)
    mock_nes_client.async_get_service_locations.return_value = [
        NESServiceLocation("7013678056", "service-1", "Electric", "123 Main St"),
        NESServiceLocation("7013672147", "service-2", "Electric", "456 Oak Ave"),
    ]

    result = await _submit_credentials(hass, await _start_user_flow(hass))
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"service_location": "7013672147:service-2"}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == "7013672147:service-2"


async def test_user_flow_duplicate_service_aborts(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    mock_nes_client: MagicMock,
) -> None:
    """The same account service cannot be configured twice."""
    MockConfigEntry(
        domain=DOMAIN,
        title="NES (123 Main St)",
        data={"username": "existing@example.com", "password": "pass"},
        unique_id="7013678056:service-1",
    ).add_to_hass(hass)

    result = await _submit_credentials(hass, await _start_user_flow(hass))

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.parametrize(
    ("exception", "expected_error"),
    [
        (NESAuthError("Bad creds"), "invalid_auth"),
        (NESConnectionError("Timeout"), "cannot_connect"),
        (RuntimeError("Boom"), "unknown"),
    ],
)
async def test_user_flow_discovery_errors(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    mock_nes_client: MagicMock,
    exception: Exception,
    expected_error: str,
) -> None:
    """Authentication and discovery failures remain on the credentials form."""
    mock_nes_client.async_authenticate.side_effect = exception

    result = await _submit_credentials(hass, await _start_user_flow(hass))

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected_error}


async def test_user_flow_no_service_locations(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    mock_nes_client: MagicMock,
) -> None:
    """An account without a usable service reports a specific error."""
    mock_nes_client.async_get_service_locations.return_value = []

    result = await _submit_credentials(hass, await _start_user_flow(hass))

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_service_locations"}


async def test_reauth_preserves_and_validates_selected_service(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    mock_nes_client: MagicMock,
) -> None:
    """New credentials do not erase or silently change the selected location."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="NES (123 Main St)",
        data={
            "username": "old@example.com",
            "password": "old-password",
            "account_number": "7013678056",
            "service_id": "service-1",
            "service_type": "Electric",
            "service_address": "123 Main St",
        },
        unique_id="7013678056:service-1",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_REAUTH,
            "entry_id": entry.entry_id,
        },
        data=entry.data,
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"username": "new@example.com", "password": "new-password"},
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["username"] == "new@example.com"
    assert entry.data["service_id"] == "service-1"
    assert entry.data["service_address"] == "123 Main St"


async def test_reauth_rejects_credentials_without_selected_service(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    mock_nes_client: MagicMock,
) -> None:
    """Reauth does not silently move the entry to another linked address."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="NES (123 Main St)",
        data={
            "username": "old@example.com",
            "password": "old-password",
            "account_number": "7013678056",
            "service_id": "missing-service",
        },
        unique_id="7013678056:missing-service",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_REAUTH,
            "entry_id": entry.entry_id,
        },
        data=entry.data,
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"username": "new@example.com", "password": "new-password"},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "account_unavailable"}
    assert entry.data["username"] == "old@example.com"


async def test_reconfigure_legacy_entry_selects_location(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    mock_nes_client: MagicMock,
) -> None:
    """An existing credential-only entry can adopt an explicit location."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="NES (7013678056)",
        data={"username": "test@example.com", "password": "testpassword"},
        unique_id="105112",
        version=1,
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
        data=entry.data,
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "account"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"service_location": "7013678056:service-1"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.unique_id == "7013678056:service-1"
    assert entry.data["service_id"] == "service-1"
