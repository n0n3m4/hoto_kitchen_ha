"""The HOTO Smart Weight scale integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import (
    CONF_IDLE_TIMEOUT,
    CONF_RECONNECT_COOLDOWN,
    CONF_TOKEN,
    DEFAULT_IDLE_TIMEOUT,
    DEFAULT_RECONNECT_COOLDOWN,
)
from .coordinator import HotoCoordinator
from .hoto import HotoScale

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]

type HotoConfigEntry = ConfigEntry[HotoCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: HotoConfigEntry) -> bool:
    """Set up HOTO Smart Weight from a config entry."""
    address: str = entry.data[CONF_ADDRESS]
    try:
        token = bytes.fromhex(entry.data[CONF_TOKEN])
    except ValueError as err:
        raise ConfigEntryNotReady(f"Invalid token for {address}") from err

    idle_timeout = entry.options.get(CONF_IDLE_TIMEOUT, DEFAULT_IDLE_TIMEOUT)
    reconnect_cooldown = entry.options.get(
        CONF_RECONNECT_COOLDOWN, DEFAULT_RECONNECT_COOLDOWN
    )
    scale = HotoScale(
        address,
        token,
        idle_timeout=idle_timeout,
        reconnect_cooldown=reconnect_cooldown,
    )
    coordinator = HotoCoordinator(hass, address, scale)
    await coordinator.async_start()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HotoConfigEntry) -> bool:
    """Unload a config entry and disconnect the scale cleanly."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.async_stop()
    return unload_ok


async def _async_update_listener(
    hass: HomeAssistant, entry: HotoConfigEntry
) -> None:
    """Apply changed options (idle timeout, reconnect cooldown) without a reload."""
    scale = entry.runtime_data.scale
    scale.set_idle_timeout(
        entry.options.get(CONF_IDLE_TIMEOUT, DEFAULT_IDLE_TIMEOUT)
    )
    scale.set_reconnect_cooldown(
        entry.options.get(CONF_RECONNECT_COOLDOWN, DEFAULT_RECONNECT_COOLDOWN)
    )
