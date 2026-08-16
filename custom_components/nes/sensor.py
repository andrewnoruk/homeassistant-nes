"""Sensor platform for Nashville Electric Service."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import NESDataUpdateCoordinator
from .data import NESConfigEntry
from .entity import NESEntity


@dataclass(frozen=True, kw_only=True)
class NESSensorEntityDescription(SensorEntityDescription):
    """Describe an NES sensor entity."""

    value_fn: Callable[[dict[str, Any]], float | None]
    attribute_fn: Callable[[dict[str, Any]], Mapping[str, Any]] | None = None


def _rate_attributes(data: dict[str, Any]) -> dict[str, Any]:
    """Return common source attributes for published rates."""
    rates = data.get("rates", {})
    return {
        key: rates[key]
        for key in (
            "effective_month",
            "source_url",
            "base_rate_url",
            "fuel_adjustment_url",
        )
        if rates.get(key) is not None
    }


def _service_charge_attributes(data: dict[str, Any]) -> dict[str, Any]:
    """Return the inputs used to select the service charge."""
    rates = data.get("rates", {})
    attributes = _rate_attributes(data)
    for key in ("service_charge_tier", "average_monthly_kwh"):
        if rates.get(key) is not None:
            attributes[key] = rates[key]
    return attributes


def _grid_charge_attributes(data: dict[str, Any]) -> dict[str, Any]:
    """Return the inputs used to select the grid access charge."""
    rates = data.get("rates", {})
    attributes = _rate_attributes(data)
    for key in ("grid_access_charge_tier", "average_monthly_kwh"):
        if rates.get(key) is not None:
            attributes[key] = rates[key]
    return attributes


SENSOR_DESCRIPTIONS: tuple[NESSensorEntityDescription, ...] = (
    NESSensorEntityDescription(
        key="monthly_energy_usage",
        translation_key="monthly_energy_usage",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=0,
        value_fn=lambda data: _safe_float(
            data.get("latest", {}).get("billedConsumption")
        ),
    ),
    NESSensorEntityDescription(
        key="monthly_energy_cost",
        translation_key="monthly_energy_cost",
        native_unit_of_measurement="USD",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        value_fn=lambda data: _safe_float(data.get("latest", {}).get("billedCharge")),
    ),
    NESSensorEntityDescription(
        key="yearly_energy_usage",
        translation_key="yearly_energy_usage",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=0,
        value_fn=lambda data: data.get("total_kwh"),
    ),
    NESSensorEntityDescription(
        key="yearly_energy_cost",
        translation_key="yearly_energy_cost",
        native_unit_of_measurement="USD",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        value_fn=lambda data: data.get("total_cost"),
    ),
    NESSensorEntityDescription(
        key="variable_energy_rate",
        translation_key="variable_energy_rate",
        native_unit_of_measurement="USD/kWh",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=5,
        value_fn=lambda data: _safe_float(data.get("rates", {}).get("variable_rate")),
        attribute_fn=_rate_attributes,
    ),
    NESSensorEntityDescription(
        key="base_energy_rate",
        translation_key="base_energy_rate",
        native_unit_of_measurement="USD/kWh",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=5,
        value_fn=lambda data: _safe_float(data.get("rates", {}).get("base_rate")),
        attribute_fn=_rate_attributes,
    ),
    NESSensorEntityDescription(
        key="fuel_cost_adjustment",
        translation_key="fuel_cost_adjustment",
        native_unit_of_measurement="USD/kWh",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=5,
        value_fn=lambda data: _safe_float(
            data.get("rates", {}).get("fuel_cost_adjustment")
        ),
        attribute_fn=_rate_attributes,
    ),
    NESSensorEntityDescription(
        key="monthly_service_charge",
        translation_key="monthly_service_charge",
        native_unit_of_measurement="USD",
        device_class=SensorDeviceClass.MONETARY,
        suggested_display_precision=2,
        value_fn=lambda data: _safe_float(data.get("rates", {}).get("service_charge")),
        attribute_fn=_service_charge_attributes,
    ),
    NESSensorEntityDescription(
        key="monthly_grid_access_charge",
        translation_key="monthly_grid_access_charge",
        native_unit_of_measurement="USD",
        device_class=SensorDeviceClass.MONETARY,
        suggested_display_precision=2,
        value_fn=lambda data: _safe_float(
            data.get("rates", {}).get("grid_access_charge")
        ),
        attribute_fn=_grid_charge_attributes,
    ),
)


def _safe_float(value: Any) -> float | None:
    """Safely convert a value to float."""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NESConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up NES sensor entities."""
    coordinator = entry.runtime_data.coordinator

    async_add_entities(
        NESSensorEntity(
            coordinator=coordinator,
            entry_id=entry.entry_id,
            entry_title=entry.title,
            description=description,
        )
        for description in SENSOR_DESCRIPTIONS
    )


class NESSensorEntity(NESEntity, SensorEntity):
    """NES sensor entity."""

    entity_description: NESSensorEntityDescription

    def __init__(
        self,
        coordinator: NESDataUpdateCoordinator,
        entry_id: str,
        description: NESSensorEntityDescription,
        entry_title: str = "NES Account",
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry_id, entry_title)
        self.entity_description = description
        self._attr_unique_id = f"{entry_id}_{description.key}"

    @property
    def native_value(self) -> float | None:
        """Return the sensor value."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Return source and tier details for rate sensors."""
        if (
            self.coordinator.data is None
            or self.entity_description.attribute_fn is None
        ):
            return None
        return self.entity_description.attribute_fn(self.coordinator.data)
