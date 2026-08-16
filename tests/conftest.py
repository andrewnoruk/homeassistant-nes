"""Fixtures for NES integration tests."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.nes.api import NESServiceLocation


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Override async_setup_entry."""
    with patch(
        "custom_components.nes.async_setup_entry",
        return_value=True,
    ) as mock:
        yield mock


@pytest.fixture
def mock_nes_client() -> Generator[MagicMock]:
    """Create a mock NES API client."""
    with patch(
        "custom_components.nes.config_flow.NESApiClient",
        autospec=True,
    ) as mock_cls:
        client = mock_cls.return_value
        client.async_authenticate = AsyncMock()
        client.async_get_customer = AsyncMock(
            return_value={
                "accountContext": {
                    "accountNumber": "7013678056",
                    "userID": "test@example.com",
                },
                "accountSummaryType": {},
            }
        )
        client.async_get_service_locations = AsyncMock(
            return_value=[
                NESServiceLocation(
                    account_number="7013678056",
                    service_id="service-1",
                    service_type="Electric",
                    service_address="123 Main St, Nashville, TN 37201",
                )
            ]
        )
        client.async_get_usage = AsyncMock(return_value=MOCK_USAGE_DATA)
        client.customer_id = "105112"
        yield client


MOCK_USAGE_DATA = [
    {
        "chargeDate": "Feb 2026",
        "chargeDateRaw": "26-Feb-2026",
        "billedConsumption": "605",
        "billedCharge": "97.09",
        "daysOfService": "28",
        "counter": "KWH",
        "uom": "kWh",
        "meterNumber": "305244",
        "avgHigh": 0,
        "avgLow": 0,
        "temp": 43,
    },
    {
        "chargeDate": "Mar 2026",
        "chargeDateRaw": "26-Mar-2026",
        "billedConsumption": "797",
        "billedCharge": "128.50",
        "daysOfService": "31",
        "counter": "KWH",
        "uom": "kWh",
        "meterNumber": "305244",
        "avgHigh": 0,
        "avgLow": 0,
        "temp": 55,
    },
    {
        "chargeDate": "Apr 2026",
        "chargeDateRaw": "26-Apr-2026",
        "billedConsumption": "293",
        "billedCharge": "52.10",
        "daysOfService": "30",
        "counter": "KWH",
        "uom": "kWh",
        "meterNumber": "305244",
        "avgHigh": 0,
        "avgLow": 0,
        "temp": 65,
    },
]

MOCK_RATE_DATA = {
    "base_rate": 0.09254,
    "fuel_cost_adjustment": 0.02610,
    "variable_rate": 0.11864,
    "effective_month": "August 2026",
    "source_url": "https://www.nespower.com/rates/",
    "base_rate_url": "https://www.nespower.com/residential.pdf",
    "fuel_adjustment_url": "https://www.nespower.com/fuel-august-2026.pdf",
    "service_charge_tiers": [
        {"tier": 1, "max_kwh": 500.0, "charge": 12.06},
        {"tier": 2, "max_kwh": 2000.0, "charge": 16.96},
        {"tier": 3, "max_kwh": 4000.0, "charge": 24.96},
        {"tier": 4, "max_kwh": 6000.0, "charge": 30.66},
        {"tier": 5, "max_kwh": None, "charge": 36.70},
    ],
    "grid_access_charge_tiers": [
        {"tier": 1, "max_kwh": 500.0, "charge": 4.50},
        {"tier": 2, "max_kwh": 2000.0, "charge": 7.33},
        {"tier": 3, "max_kwh": 4000.0, "charge": 7.33},
        {"tier": 4, "max_kwh": 6000.0, "charge": 7.88},
        {"tier": 5, "max_kwh": None, "charge": 7.88},
    ],
}
