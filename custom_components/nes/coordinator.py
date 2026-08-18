"""DataUpdateCoordinator for the NES integration."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

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


def _usage_period(
    daily_usage: list[dict[str, Any]] | None,
    period_start: date,
    period_end: date,
) -> dict[str, Any]:
    """Aggregate normalized daily readings over an inclusive calendar period."""
    result: dict[str, Any] = {
        "usage_kwh": None,
        "period_start": period_start.isoformat(),
        "first_reading_date": None,
        "data_through": None,
        "readings_count": 0,
    }
    if daily_usage is None:
        return result

    readings: list[tuple[date, float]] = []
    for item in daily_usage:
        try:
            usage_date = date.fromisoformat(str(item.get("usageDate")))
            usage_value = float(item["usageConsumptionValue"])
        except (KeyError, TypeError, ValueError):
            continue
        if period_start <= usage_date <= period_end:
            readings.append((usage_date, usage_value))

    result["usage_kwh"] = round(sum(value for _, value in readings), 4)
    result["readings_count"] = len(readings)
    if readings:
        dates = [usage_date for usage_date, _ in readings]
        result["first_reading_date"] = min(dates).isoformat()
        result["data_through"] = max(dates).isoformat()
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

        daily_usage = self.client.daily_usage
        if not isinstance(daily_usage, list):
            daily_usage = None
        today = dt_util.now().date()
        month_to_date = _usage_period(daily_usage, today.replace(day=1), today)
        year_to_date = _usage_period(daily_usage, today.replace(month=1, day=1), today)

        return {
            "monthly": usage_data,
            "latest": latest,
            "total_kwh": round(total_kwh, 2),
            "total_cost": round(total_cost, 2),
            "month_to_date": month_to_date,
            "year_to_date": year_to_date,
            "rates": _enrich_rates(rates, usage_data),
        }
