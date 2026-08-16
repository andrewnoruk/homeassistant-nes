"""Nashville Electric Service (NES) integration for Home Assistant."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.loader import async_get_loaded_integration

from .api import NESApiClient, NESApiError, NESAuthError, NESConnectionError
from .const import CONF_ACCOUNT_NUMBER, CONF_SERVICE_ID
from .coordinator import NESDataUpdateCoordinator
from .data import NESConfigEntry, NESData

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: NESConfigEntry) -> bool:
    """Set up NES from a config entry."""
    client = NESApiClient(
        username=entry.data["username"],
        password=entry.data["password"],
        session=async_get_clientsession(hass),
    )

    # Authenticate and fetch account info
    try:
        await client.async_authenticate()
        await client.async_get_customer(
            account_number=entry.data.get(CONF_ACCOUNT_NUMBER),
            service_id=entry.data.get(CONF_SERVICE_ID),
        )
    except NESAuthError as err:
        raise ConfigEntryAuthFailed(f"Authentication failed: {err}") from err
    except (NESApiError, NESConnectionError) as err:
        raise ConfigEntryNotReady(f"Cannot connect to NES: {err}") from err

    coordinator = NESDataUpdateCoordinator(hass, client)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = NESData(
        client=client,
        coordinator=coordinator,
        integration=async_get_loaded_integration(hass, entry.domain),
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: NESConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reload_entry(hass: HomeAssistant, entry: NESConfigEntry) -> None:
    """Reload the config entry."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_entry(hass: HomeAssistant, entry: NESConfigEntry) -> bool:
    """Migrate legacy entries while preserving their default-account behavior."""
    if entry.version == 1:
        hass.config_entries.async_update_entry(entry, version=2)

    return True
