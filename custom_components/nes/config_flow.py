"""Config flow for Nashville Electric Service."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import NESApiClient, NESAuthError, NESConnectionError
from .const import DOMAIN, LOGGER

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("username"): str,
        vol.Required("password"): str,
    }
)


class NESConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for NES."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            session = async_create_clientsession(self.hass)
            client = NESApiClient(
                username=user_input["username"],
                password=user_input["password"],
                session=session,
            )

            try:
                await client.async_authenticate()
                account = await client.async_get_customer()
            except NESAuthError:
                errors["base"] = "invalid_auth"
            except NESConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                LOGGER.exception("Unexpected error during config flow")
                errors["base"] = "unknown"
            else:
                customer_id = client.customer_id or user_input["username"]
                await self.async_set_unique_id(str(customer_id))
                self._abort_if_unique_id_configured()

                acct_num = account.get("accountContext", {}).get("accountNumber", "")
                title = f"NES ({acct_num})" if acct_num else f"NES ({customer_id})"

                return self.async_create_entry(
                    title=title,
                    data={
                        "username": user_input["username"],
                        "password": user_input["password"],
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Handle re-authentication."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle re-authentication confirmation."""
        errors: dict[str, str] = {}

        if user_input is not None:
            session = async_create_clientsession(self.hass)
            client = NESApiClient(
                username=user_input["username"],
                password=user_input["password"],
                session=session,
            )

            try:
                await client.async_authenticate()
                await client.async_get_customer()
            except NESAuthError:
                errors["base"] = "invalid_auth"
            except NESConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                LOGGER.exception("Unexpected error during reauth")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(),
                    data={
                        "username": user_input["username"],
                        "password": user_input["password"],
                    },
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )
