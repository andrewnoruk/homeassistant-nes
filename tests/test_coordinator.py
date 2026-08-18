"""Tests for the NES data update coordinator."""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from conftest import MOCK_RATE_DATA, MOCK_USAGE_DATA
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.nes.api import (
    NESApiClient,
    NESApiError,
    NESAuthError,
    NESConnectionError,
)
from custom_components.nes.coordinator import (
    NESDataUpdateCoordinator,
    _enrich_rates,
    _usage_period,
)


async def test_coordinator_successful_update(hass: HomeAssistant) -> None:
    """Test coordinator fetches and processes data correctly."""
    client = MagicMock(spec=NESApiClient)
    client.async_get_usage = AsyncMock(return_value=MOCK_USAGE_DATA)
    client.async_get_rates = AsyncMock(return_value=MOCK_RATE_DATA)
    client.daily_usage = [
        {"usageDate": "2025-12-31", "usageConsumptionValue": 99},
        {"usageDate": "2026-01-01", "usageConsumptionValue": 10},
        {"usageDate": "2026-07-31", "usageConsumptionValue": 20},
        {"usageDate": "2026-08-01", "usageConsumptionValue": 30},
        {"usageDate": "2026-08-15", "usageConsumptionValue": 40},
        {"usageDate": "2026-08-16", "usageConsumptionValue": 50},
        {"usageDate": "2026-08-18", "usageConsumptionValue": 100},
    ]

    coordinator = NESDataUpdateCoordinator(hass, client)
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "custom_components.nes.coordinator.dt_util.now",
            lambda: datetime(2026, 8, 17, 12, 0),
        )
        data = await coordinator._async_update_data()

    assert len(data["monthly"]) == 3
    assert data["latest"]["chargeDate"] == "Apr 2026"
    assert data["total_kwh"] == pytest.approx(1695.0, abs=1)
    assert data["total_cost"] == pytest.approx(277.69, abs=0.01)
    assert data["rates"]["variable_rate"] == pytest.approx(0.11864)
    assert data["rates"]["service_charge"] == pytest.approx(16.96)
    assert data["rates"]["service_charge_tier"] == 2
    assert data["rates"]["grid_access_charge"] == pytest.approx(7.33)
    assert data["rates"]["grid_access_charge_tier"] == 2
    assert data["month_to_date"] == {
        "usage_kwh": 120.0,
        "period_start": "2026-08-01",
        "first_reading_date": "2026-08-01",
        "data_through": "2026-08-16",
        "readings_count": 3,
    }
    assert data["year_to_date"] == {
        "usage_kwh": 150.0,
        "period_start": "2026-01-01",
        "first_reading_date": "2026-01-01",
        "data_through": "2026-08-16",
        "readings_count": 5,
    }


async def test_coordinator_empty_data(hass: HomeAssistant) -> None:
    """Test coordinator handles empty usage data."""
    client = MagicMock(spec=NESApiClient)
    client.async_get_usage = AsyncMock(return_value=[])
    client.async_get_rates = AsyncMock(return_value=MOCK_RATE_DATA)

    coordinator = NESDataUpdateCoordinator(hass, client)
    data = await coordinator._async_update_data()

    assert data["monthly"] == []
    assert data["latest"] == {}
    assert data["total_kwh"] == 0.0
    assert data["total_cost"] == 0.0
    assert data["rates"]["variable_rate"] == pytest.approx(0.11864)
    assert data["rates"]["service_charge"] is None


async def test_coordinator_auth_error_raises_reauth(
    hass: HomeAssistant,
) -> None:
    """Test coordinator converts auth errors to ConfigEntryAuthFailed."""
    client = MagicMock(spec=NESApiClient)
    client.async_get_usage = AsyncMock(side_effect=NESAuthError("Token expired"))

    coordinator = NESDataUpdateCoordinator(hass, client)

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_coordinator_connection_error_raises_update_failed(
    hass: HomeAssistant,
) -> None:
    """Test coordinator converts connection errors to UpdateFailed."""
    client = MagicMock(spec=NESApiClient)
    client.async_get_usage = AsyncMock(side_effect=NESConnectionError("Network down"))

    coordinator = NESDataUpdateCoordinator(hass, client)

    with pytest.raises(UpdateFailed, match="Error communicating"):
        await coordinator._async_update_data()


async def test_coordinator_handles_malformed_values(
    hass: HomeAssistant,
) -> None:
    """Test coordinator handles non-numeric values gracefully."""
    bad_data = [
        {"chargeDate": "Mar 2026", "billedConsumption": "N/A", "billedCharge": None},
        {"chargeDate": "Apr 2026", "billedConsumption": "500", "billedCharge": "85.00"},
    ]
    client = MagicMock(spec=NESApiClient)
    client.async_get_usage = AsyncMock(return_value=bad_data)
    client.async_get_rates = AsyncMock(return_value=MOCK_RATE_DATA)

    coordinator = NESDataUpdateCoordinator(hass, client)
    data = await coordinator._async_update_data()

    assert data["total_kwh"] == pytest.approx(500.0, abs=0.01)
    assert data["total_cost"] == pytest.approx(85.00, abs=0.01)


async def test_coordinator_keeps_usage_when_rates_are_unavailable(
    hass: HomeAssistant,
) -> None:
    """Test a public rates-page failure does not discard account usage."""
    client = MagicMock(spec=NESApiClient)
    client.async_get_usage = AsyncMock(return_value=MOCK_USAGE_DATA)
    client.async_get_rates = AsyncMock(side_effect=NESApiError("Page changed"))

    coordinator = NESDataUpdateCoordinator(hass, client)
    data = await coordinator._async_update_data()

    assert data["latest"]["chargeDate"] == "Apr 2026"
    assert "variable_rate" not in data["rates"]


def test_rate_tiers_use_average_of_latest_twelve_bills() -> None:
    """Test both fixed charges use average usage from the latest twelve bills."""
    usage_data = [{"billedConsumption": "7000"}] + [
        {"billedConsumption": "100"} for _ in range(12)
    ]

    rates = _enrich_rates(MOCK_RATE_DATA, usage_data)

    assert rates["average_monthly_kwh"] == pytest.approx(100.0)
    assert rates["service_charge"] == pytest.approx(12.06)
    assert rates["grid_access_charge"] == pytest.approx(4.50)


def test_usage_period_distinguishes_unsupported_from_no_current_readings() -> None:
    """Legacy data is unavailable while a supported empty period totals zero."""
    unsupported = _usage_period(None, date(2026, 8, 1), date(2026, 8, 17))
    supported = _usage_period(
        [{"usageDate": "2026-07-31", "usageConsumptionValue": 12}],
        date(2026, 8, 1),
        date(2026, 8, 17),
    )

    assert unsupported["usage_kwh"] is None
    assert supported["usage_kwh"] == 0.0
    assert supported["readings_count"] == 0
    assert supported["data_through"] is None
