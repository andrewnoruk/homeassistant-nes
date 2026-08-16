"""DataUpdateCoordinator for the NES integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import NESApiClient, NESApiError, NESAuthError, NESConnectionError
from .const import LOGGER, UPDATE_INTERVAL_HOURS


def _safe_float_or_zero(value: Any) -> float:
    """Safely convert a value to float, defaulting to 0.0."""
    if value is None:
        return 0.0
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0


def _safe_float_or_none(value: Any) -> float | None:
    """Safely convert a value to float, defaulting to None."""
    try:
        return float(value) if value is not None else None
    except (ValueError, TypeError):
        return None


def _select_charge(
    tiers: list[dict[str, Any]], usage_kwh: float | None
) -> tuple[float | None, int | None]:
    """Select a fixed charge and tier for the supplied usage."""
    if usage_kwh is None:
        return None, None
    for tier in tiers:
        max_kwh = tier.get("max_kwh")
        if max_kwh is None or usage_kwh <= max_kwh:
            return _safe_float_or_none(tier.get("charge")), tier.get("tier")
    return None, None


def _enrich_rates(
    rates: dict[str, Any], usage_data: list[dict[str, Any]]
) -> dict[str, Any]:
    """Add account-specific fixed charges to published rate data."""
    result = dict(rates)
    recent_usage = [
        value
        for item in usage_data[-12:]
        if (value := _safe_float_or_none(item.get("billedConsumption"))) is not None
        and value >= 0
    ]
    average_kwh = sum(recent_usage) / len(recent_usage) if recent_usage else None

    service_charge, service_tier = _select_charge(
        result.get("service_charge_tiers", []), average_kwh
    )
    grid_charge, grid_tier = _select_charge(
        result.get("grid_access_charge_tiers", []), average_kwh
    )
    result.update(
        {
            "average_monthly_kwh": (
                round(average_kwh, 2) if average_kwh is not None else None
            ),
            "service_charge": service_charge,
            "service_charge_tier": service_tier,
            "grid_access_charge": grid_charge,
            "grid_access_charge_tier": grid_tier,
        }
    )
    return result


class NESDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator to manage fetching NES usage data."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        client: NESApiClient,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            LOGGER,
            name="NES Usage Data",
            update_interval=timedelta(hours=UPDATE_INTERVAL_HOURS),
        )
        self.client = client

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from the NES API."""
        try:
            usage_data = await self.client.async_get_usage()
        except NESAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain="nes",
                translation_key="auth_failed",
            ) from err
        except (NESApiError, NESConnectionError) as err:
            raise UpdateFailed(f"Error communicating with NES API: {err}") from err

        try:
            rates = await self.client.async_get_rates()
        except (NESApiError, NESConnectionError) as err:
            LOGGER.warning("Unable to update NES rates: %s", err)
            rates = self.data.get("rates", {}) if self.data else {}

        # Data comes as monthly history sorted chronologically
        # Each item: chargeDate, billedConsumption, billedCharge, etc.
        latest = usage_data[-1] if usage_data else {}

        total_kwh = sum(
            _safe_float_or_zero(m.get("billedConsumption")) for m in usage_data
        )
        total_cost = sum(_safe_float_or_zero(m.get("billedCharge")) for m in usage_data)

        return {
            "monthly": usage_data,
            "latest": latest,
            "total_kwh": round(total_kwh, 2),
            "total_cost": round(total_cost, 2),
            "rates": _enrich_rates(rates, usage_data),
        }
