"""Config flow for Nashville Electric Service."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import (
    NESApiClient,
    NESApiError,
    NESAuthError,
    NESConnectionError,
    NESServiceLocation,
)
from .const import (
    CONF_ACCOUNT_NUMBER,
    CONF_SERVICE_ADDRESS,
    CONF_SERVICE_ID,
    CONF_SERVICE_LOCATION,
    CONF_SERVICE_TYPE,
    DOMAIN,
    LOGGER,
)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class NESConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for NES."""

    VERSION = 2

    _credentials: dict[str, str] | None = None
    _locations: dict[str, NESServiceLocation] | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Authenticate and discover the login's service locations."""
        errors: dict[str, str] = {}

        if user_input is not None:
            credentials = {
                CONF_USERNAME: user_input[CONF_USERNAME],
                CONF_PASSWORD: user_input[CONF_PASSWORD],
            }
            try:
                locations = await self._async_discover_locations(credentials)
            except NESAuthError:
                errors["base"] = "invalid_auth"
            except (NESApiError, NESConnectionError):
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                LOGGER.exception("Unexpected error during config flow")
                errors["base"] = "unknown"
            else:
                if not locations:
                    errors["base"] = "no_service_locations"
                else:
                    self._credentials = credentials
                    self._locations = {
                        location.unique_id: location for location in locations
                    }
                    if len(self._locations) == 1:
                        return await self._async_finish_location(
                            next(iter(self._locations.values()))
                        )
                    return await self.async_step_account()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_account(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user select a linked account service location."""
        if not self._locations or not self._credentials:
            return self.async_abort(reason="no_service_locations")

        if user_input is not None:
            location = self._locations.get(user_input[CONF_SERVICE_LOCATION])
            if location is None:
                return self.async_show_form(
                    step_id="account",
                    data_schema=self._account_schema(),
                    errors={"base": "account_unavailable"},
                )
            return await self._async_finish_location(location)

        return self.async_show_form(
            step_id="account",
            data_schema=self._account_schema(),
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Rediscover and change the service used by an existing entry."""
        entry = self._get_reconfigure_entry()
        credentials = {
            CONF_USERNAME: entry.data[CONF_USERNAME],
            CONF_PASSWORD: entry.data[CONF_PASSWORD],
        }
        errors: dict[str, str] = {}

        try:
            locations = await self._async_discover_locations(credentials)
        except NESAuthError:
            errors["base"] = "invalid_auth"
        except (NESApiError, NESConnectionError):
            errors["base"] = "cannot_connect"
        except Exception:  # noqa: BLE001
            LOGGER.exception("Unexpected error during NES reconfiguration")
            errors["base"] = "unknown"
        else:
            if not locations:
                errors["base"] = "no_service_locations"
            else:
                self._credentials = credentials
                self._locations = {
                    location.unique_id: location for location in locations
                }
                return await self.async_step_account()

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema({}),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Handle re-authentication."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Update credentials without changing the selected service."""
        errors: dict[str, str] = {}

        if user_input is not None:
            entry = self._get_reauth_entry()
            credentials = {
                CONF_USERNAME: user_input[CONF_USERNAME],
                CONF_PASSWORD: user_input[CONF_PASSWORD],
            }
            client = self._client(credentials)

            try:
                await client.async_authenticate()
                if CONF_SERVICE_ID in entry.data:
                    locations = await client.async_get_service_locations()
                    selected_id = self._location_unique_id(
                        entry.data[CONF_ACCOUNT_NUMBER],
                        entry.data[CONF_SERVICE_ID],
                    )
                    if not any(
                        location.unique_id == selected_id for location in locations
                    ):
                        errors["base"] = "account_unavailable"
                else:
                    await client.async_get_customer()
            except NESAuthError:
                errors["base"] = "invalid_auth"
            except NESConnectionError:
                errors["base"] = "cannot_connect"
            except NESApiError:
                errors["base"] = (
                    "account_unavailable"
                    if CONF_SERVICE_ID in entry.data
                    else "cannot_connect"
                )
            except Exception:  # noqa: BLE001
                LOGGER.exception("Unexpected error during reauth")
                errors["base"] = "unknown"
            else:
                if not errors:
                    return self.async_update_reload_and_abort(
                        entry,
                        data={**entry.data, **credentials},
                    )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def _async_discover_locations(
        self, credentials: dict[str, str]
    ) -> list[NESServiceLocation]:
        """Authenticate and retrieve all linked service locations."""
        client = self._client(credentials)
        await client.async_authenticate()
        return await client.async_get_service_locations()

    def _client(self, credentials: dict[str, str]) -> NESApiClient:
        """Build an API client for a config-flow request."""
        return NESApiClient(
            username=credentials[CONF_USERNAME],
            password=credentials[CONF_PASSWORD],
            session=async_create_clientsession(self.hass),
        )

    def _account_schema(self) -> vol.Schema:
        """Build the account selector schema from discovered locations."""
        assert self._locations
        return vol.Schema(
            {
                vol.Required(CONF_SERVICE_LOCATION): vol.In(
                    {
                        unique_id: location.display_name
                        for unique_id, location in self._locations.items()
                    }
                )
            }
        )

    async def _async_finish_location(
        self, location: NESServiceLocation
    ) -> ConfigFlowResult:
        """Create or update an entry for the chosen service location."""
        assert self._credentials
        title = self._entry_title(location)
        data = {
            **self._credentials,
            CONF_ACCOUNT_NUMBER: location.account_number,
            CONF_SERVICE_ID: location.service_id,
            CONF_SERVICE_TYPE: location.service_type,
            CONF_SERVICE_ADDRESS: location.service_address,
        }

        if self.source == config_entries.SOURCE_RECONFIGURE:
            entry = self._get_reconfigure_entry()
            duplicate = self.hass.config_entries.async_entry_for_domain_unique_id(
                DOMAIN, location.unique_id
            )
            if duplicate is not None and duplicate.entry_id != entry.entry_id:
                return self.async_abort(reason="already_configured")
            return self.async_update_reload_and_abort(
                entry,
                unique_id=location.unique_id,
                title=title,
                data=data,
            )

        await self.async_set_unique_id(location.unique_id)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=title, data=data)

    @staticmethod
    def _location_unique_id(account_number: str, service_id: str) -> str:
        """Build the config entry unique ID for an NES service."""
        return f"{account_number}:{service_id}"

    @staticmethod
    def _entry_title(location: NESServiceLocation) -> str:
        """Build a title that distinguishes service locations."""
        if location.service_address:
            return f"NES ({location.service_address})"
        return f"NES ({location.account_number})"
