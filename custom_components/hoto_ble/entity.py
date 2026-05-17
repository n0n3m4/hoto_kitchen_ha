"""Base entity for the HOTO Smart Weight integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import HotoCoordinator


class HotoEntity(Entity):
    """Base class binding an entity to a :class:`HotoCoordinator`."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator: HotoCoordinator) -> None:
        """Initialise the entity for the given scale."""
        self.coordinator = coordinator
        address = coordinator.address
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, address)},
            connections={(CONNECTION_BLUETOOTH, address)},
            manufacturer=MANUFACTURER,
            model=MODEL,
            name=f"{MANUFACTURER} {MODEL}",
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to coordinator updates."""
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )
