"""Tests for NES sensor entities."""

from __future__ import annotations

import pytest
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass

from custom_components.nes.sensor import (
    SENSOR_DESCRIPTIONS,
    _safe_float,
)


class TestSafeFloat:
    """Test the _safe_float helper."""

    def test_valid_float(self) -> None:
        assert _safe_float("19.0362") == pytest.approx(19.0362)

    def test_valid_int_string(self) -> None:
        assert _safe_float("82") == pytest.approx(82.0)

    def test_none(self) -> None:
        assert _safe_float(None) is None

    def test_empty_string(self) -> None:
        assert _safe_float("") is None

    def test_non_numeric(self) -> None:
        assert _safe_float("N/A") is None

    def test_numeric_zero(self) -> None:
        assert _safe_float("0") == pytest.approx(0.0)

    def test_actual_float(self) -> None:
        assert _safe_float(25.5) == pytest.approx(25.5)


class TestSensorDescriptions:
    """Test sensor entity descriptions are correct."""

    def test_correct_number_of_sensors(self) -> None:
        assert len(SENSOR_DESCRIPTIONS) == 9

    def test_monthly_energy_is_energy_dashboard_compatible(self) -> None:
        monthly = next(
            s for s in SENSOR_DESCRIPTIONS if s.key == "monthly_energy_usage"
        )
        assert monthly.device_class == SensorDeviceClass.ENERGY
        assert monthly.state_class == SensorStateClass.TOTAL
        assert monthly.native_unit_of_measurement == "kWh"

    def test_monthly_cost_is_monetary(self) -> None:
        cost = next(s for s in SENSOR_DESCRIPTIONS if s.key == "monthly_energy_cost")
        assert cost.device_class == SensorDeviceClass.MONETARY

    def test_yearly_energy_is_energy_class(self) -> None:
        yearly = next(s for s in SENSOR_DESCRIPTIONS if s.key == "yearly_energy_usage")
        assert yearly.device_class == SensorDeviceClass.ENERGY

    def test_yearly_cost_is_monetary(self) -> None:
        cost = next(s for s in SENSOR_DESCRIPTIONS if s.key == "yearly_energy_cost")
        assert cost.device_class == SensorDeviceClass.MONETARY


class TestSensorValues:
    """Test sensor value extraction from coordinator data."""

    def _make_data(self) -> dict:
        return {
            "monthly": [],
            "latest": {
                "billedConsumption": "293",
                "billedCharge": "52.10",
            },
            "total_kwh": 1695.0,
            "total_cost": 277.69,
            "rates": {
                "base_rate": 0.09254,
                "fuel_cost_adjustment": 0.02610,
                "variable_rate": 0.11864,
                "effective_month": "August 2026",
                "source_url": "https://www.nespower.com/rates/",
                "service_charge": 16.96,
                "service_charge_tier": 2,
                "grid_access_charge": 7.33,
                "grid_access_charge_tier": 2,
                "average_monthly_kwh": 565.0,
            },
        }

    def test_monthly_energy_value(self) -> None:
        data = self._make_data()
        desc = next(s for s in SENSOR_DESCRIPTIONS if s.key == "monthly_energy_usage")
        assert desc.value_fn(data) == pytest.approx(293.0)

    def test_monthly_cost_value(self) -> None:
        data = self._make_data()
        desc = next(s for s in SENSOR_DESCRIPTIONS if s.key == "monthly_energy_cost")
        assert desc.value_fn(data) == pytest.approx(52.10)

    def test_yearly_energy_value(self) -> None:
        data = self._make_data()
        desc = next(s for s in SENSOR_DESCRIPTIONS if s.key == "yearly_energy_usage")
        assert desc.value_fn(data) == pytest.approx(1695.0)

    def test_yearly_cost_value(self) -> None:
        data = self._make_data()
        desc = next(s for s in SENSOR_DESCRIPTIONS if s.key == "yearly_energy_cost")
        assert desc.value_fn(data) == pytest.approx(277.69)

    @pytest.mark.parametrize(
        ("key", "expected"),
        [
            ("variable_energy_rate", 0.11864),
            ("base_energy_rate", 0.09254),
            ("fuel_cost_adjustment", 0.02610),
            ("monthly_service_charge", 16.96),
            ("monthly_grid_access_charge", 7.33),
        ],
    )
    def test_rate_values(self, key: str, expected: float) -> None:
        data = self._make_data()
        desc = next(sensor for sensor in SENSOR_DESCRIPTIONS if sensor.key == key)
        assert desc.value_fn(data) == pytest.approx(expected)

    def test_fixed_charge_attributes_explain_tier_selection(self) -> None:
        data = self._make_data()
        service = next(
            sensor
            for sensor in SENSOR_DESCRIPTIONS
            if sensor.key == "monthly_service_charge"
        )
        grid = next(
            sensor
            for sensor in SENSOR_DESCRIPTIONS
            if sensor.key == "monthly_grid_access_charge"
        )

        assert service.attribute_fn is not None
        assert service.attribute_fn(data)["service_charge_tier"] == 2
        assert service.attribute_fn(data)["average_monthly_kwh"] == 565.0
        assert grid.attribute_fn is not None
        assert grid.attribute_fn(data)["grid_access_charge_tier"] == 2
        assert grid.attribute_fn(data)["average_monthly_kwh"] == 565.0

    def test_values_with_none_data(self) -> None:
        data = {
            "monthly": [],
            "latest": {},
            "total_kwh": 0.0,
            "total_cost": 0.0,
            "rates": {},
        }
        for desc in SENSOR_DESCRIPTIONS:
            value = desc.value_fn(data)
            assert value is None or isinstance(value, float)
