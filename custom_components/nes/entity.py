"""Base entity for the NES integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN
from .coordinator import NESDataUpdateCoordinator


class NESEntity(CoordinatorEntity[NESDataUpdateCoordinator]):
    """Base entity for NES."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: NESDataUpdateCoordinator,
        entry_id: str,
        entry_title: str = "NES Account",
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._attr_device_info = DeviceInfo(
            entry_type=DeviceEntryType.SERVICE,
            identifiers={(DOMAIN, entry_id)},
            manufacturer="Nashville Electric Service",
            name=entry_title,
        )
