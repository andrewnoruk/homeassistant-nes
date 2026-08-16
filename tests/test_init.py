"""Tests for NES config entry setup and migration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nes import async_migrate_entry, async_setup_entry
from custom_components.nes.const import DOMAIN


async def test_setup_restores_selected_service(hass: HomeAssistant) -> None:
    """Runtime setup initializes the exact account and service saved by setup."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="NES (456 Oak Ave)",
        data={
            "username": "test@example.com",
            "password": "password",
            "account_number": "7013672147",
            "service_id": "service-2",
            "service_type": "Electric",
            "service_address": "456 Oak Ave",
        },
        unique_id="7013672147:service-2",
        version=2,
    )
    entry.add_to_hass(hass)

    with (
        patch("custom_components.nes.NESApiClient", autospec=True) as client_cls,
        patch(
            "custom_components.nes.NESDataUpdateCoordinator", autospec=True
        ) as coordinator_cls,
        patch(
            "custom_components.nes.async_get_loaded_integration",
            new=AsyncMock(return_value=MagicMock()),
        ),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new=AsyncMock(return_value=True),
        ),
    ):
        client = client_cls.return_value
        client.async_authenticate = AsyncMock()
        client.async_get_customer = AsyncMock()
        coordinator_cls.return_value.async_config_entry_first_refresh = AsyncMock()

        assert await async_setup_entry(hass, entry)

    client.async_get_customer.assert_awaited_once_with(
        account_number="7013672147",
        service_id="service-2",
    )


async def test_setup_keeps_legacy_default_account_behavior(
    hass: HomeAssistant,
) -> None:
    """Credential-only entries continue using the NES default service."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="NES (7013678056)",
        data={"username": "test@example.com", "password": "password"},
        unique_id="105112",
        version=1,
    )
    entry.add_to_hass(hass)

    with (
        patch("custom_components.nes.NESApiClient", autospec=True) as client_cls,
        patch(
            "custom_components.nes.NESDataUpdateCoordinator", autospec=True
        ) as coordinator_cls,
        patch(
            "custom_components.nes.async_get_loaded_integration",
            new=AsyncMock(return_value=MagicMock()),
        ),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new=AsyncMock(return_value=True),
        ),
    ):
        client = client_cls.return_value
        client.async_authenticate = AsyncMock()
        client.async_get_customer = AsyncMock()
        coordinator_cls.return_value.async_config_entry_first_refresh = AsyncMock()

        assert await async_setup_entry(hass, entry)

    client.async_get_customer.assert_awaited_once_with(
        account_number=None,
        service_id=None,
    )


async def test_migrate_legacy_entry_without_guessing_service(
    hass: HomeAssistant,
) -> None:
    """Migration preserves legacy data until the user explicitly reconfigures."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"username": "test@example.com", "password": "password"},
        unique_id="105112",
        version=1,
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry)

    assert entry.version == 2
    assert "service_id" not in entry.data
