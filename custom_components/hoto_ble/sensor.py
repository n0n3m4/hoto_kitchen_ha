"""Weight sensor for the HOTO Smart Weight scale."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfMass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HotoConfigEntry
from .entity import HotoEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HotoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the weight sensor for a config entry."""
    async_add_entities([HotoWeightSensor(entry.runtime_data)])


class HotoWeightSensor(HotoEntity, SensorEntity):
    """A single live-updating weight reading from the scale."""

    _attr_device_class = SensorDeviceClass.WEIGHT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfMass.GRAMS
    _attr_suggested_display_precision = 1
    _attr_translation_key = "weight"

    def __init__(self, coordinator: Any) -> None:
        """Initialise the weight sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_weight"

    @property
    def native_value(self) -> float | None:
        """Last decoded weight in grams."""
        return self.coordinator.state.weight

    @property
    def available(self) -> bool:
        """Available once a reading exists.

        The reading is retained while the scale sleeps after an idle
        disconnect; live status is exposed via the ``connected`` and
        ``stabilized`` attributes instead of flapping availability.
        """
        return self.coordinator.state.weight is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose live-session status alongside the weight value."""
        state = self.coordinator.state
        return {
            "connected": state.connected,
            "stabilized": state.stabilized,
        }
