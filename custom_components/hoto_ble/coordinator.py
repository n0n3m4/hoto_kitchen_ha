"""Push coordinator wiring a HotoScale driver to Home Assistant."""

from __future__ import annotations

import logging
from collections.abc import Callable

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant, callback

from .hoto import HotoScale, HotoScaleState

_LOGGER = logging.getLogger(__name__)


class HotoCoordinator:
    """Owns a :class:`HotoScale`, feeds it advertisements, fans out updates."""

    def __init__(self, hass: HomeAssistant, address: str, scale: HotoScale) -> None:
        """Initialise the coordinator for a single scale."""
        self.hass = hass
        self.address = address
        self.scale = scale
        self._listeners: list[Callable[[], None]] = []
        self._unsubs: list[Callable[[], None]] = []

    @property
    def state(self) -> HotoScaleState:
        """Latest pushed state from the scale."""
        return self.scale.state

    async def async_start(self) -> None:
        """Begin the connection loop and subscribe to BLE advertisements."""
        # Seed the driver with whatever the bluetooth stack already knows.
        device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if device is not None:
            self.scale.signal_available(device)

        # Every advertisement re-opens the reconnect gate: this is how the
        # scale comes back online after an idle disconnect (user wakes it).
        self._unsubs.append(
            bluetooth.async_register_callback(
                self.hass,
                self._async_on_advertisement,
                bluetooth.BluetoothCallbackMatcher(
                    address=self.address, connectable=True
                ),
                bluetooth.BluetoothScanningMode.ACTIVE,
            )
        )
        self.scale.register_callback(self._on_scale_state)
        await self.scale.async_start()

    async def async_stop(self) -> None:
        """Tear down: stop the driver and unsubscribe callbacks."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        await self.scale.async_stop()

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Register an entity update listener; returns an unsubscribe callable."""
        self._listeners.append(listener)

        def _remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _remove

    @callback
    def _async_on_advertisement(
        self,
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        """Hand a fresh BLEDevice to the driver and unblock reconnection."""
        self.scale.signal_available(service_info.device)

    @callback
    def _on_scale_state(self, _state: HotoScaleState) -> None:
        """Forward driver state changes to all entity listeners."""
        for listener in list(self._listeners):
            listener()
