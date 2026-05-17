"""Config and options flow for the HOTO Smart Weight integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback

from .const import (
    CONF_IDLE_TIMEOUT,
    CONF_RECONNECT_COOLDOWN,
    CONF_TOKEN,
    DEFAULT_IDLE_TIMEOUT,
    DEFAULT_RECONNECT_COOLDOWN,
    DOMAIN,
    MANUFACTURER,
    MODEL,
)

# The per-device secret is a 12-byte value, i.e. 24 hex characters.
_TOKEN_HEX_LEN = 24


def _normalise_token(raw: str) -> str | None:
    """Return a cleaned hex token, or None if it is not valid."""
    cleaned = raw.strip().replace(":", "").replace(" ", "").lower()
    if len(cleaned) != _TOKEN_HEX_LEN:
        return None
    try:
        bytes.fromhex(cleaned)
    except ValueError:
        return None
    return cleaned


class HotoConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle discovery and manual setup of a HOTO scale."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise transient discovery state."""
        self._discovery: BluetoothServiceInfoBleak | None = None

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a scale discovered via Bluetooth advertisement."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._discovery = discovery_info
        self.context["title_placeholders"] = {
            "name": discovery_info.name or f"{MANUFACTURER} {MODEL}"
        }
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the device token to finish a discovered setup."""
        assert self._discovery is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            token = _normalise_token(user_input[CONF_TOKEN])
            if token is None:
                errors[CONF_TOKEN] = "invalid_token"
            else:
                return self.async_create_entry(
                    title=self._discovery.name or f"{MANUFACTURER} {MODEL}",
                    data={
                        CONF_ADDRESS: self._discovery.address,
                        CONF_TOKEN: token,
                    },
                )
        return self.async_show_form(
            step_id="bluetooth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): str}),
            description_placeholders={
                "name": self._discovery.name or self._discovery.address
            },
            errors=errors,
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle manual setup (address + token)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            address = user_input[CONF_ADDRESS].strip().upper()
            token = _normalise_token(user_input[CONF_TOKEN])
            if token is None:
                errors[CONF_TOKEN] = "invalid_token"
            else:
                await self.async_set_unique_id(
                    address, raise_on_progress=False
                )
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"{MANUFACTURER} {MODEL}",
                    data={CONF_ADDRESS: address, CONF_TOKEN: token},
                )

        # Offer any not-yet-configured discovered devices as suggestions.
        configured = self._async_current_ids()
        suggestions = [
            info.address
            for info in async_discovered_service_info(self.hass)
            if info.address not in configured
        ]
        default_address = suggestions[0] if suggestions else ""
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_ADDRESS, default=default_address
                    ): str,
                    vol.Required(CONF_TOKEN): str,
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: Any) -> OptionsFlow:
        """Return the options flow handler."""
        return HotoOptionsFlow()


class HotoOptionsFlow(OptionsFlow):
    """Let the user tune the idle-disconnect timeout."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the idle timeout option."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        idle_timeout = options.get(CONF_IDLE_TIMEOUT, DEFAULT_IDLE_TIMEOUT)
        cooldown = options.get(
            CONF_RECONNECT_COOLDOWN, DEFAULT_RECONNECT_COOLDOWN
        )
        seconds = vol.All(vol.Coerce(int), vol.Range(min=0, max=3600))
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_IDLE_TIMEOUT, default=idle_timeout
                    ): seconds,
                    vol.Required(
                        CONF_RECONNECT_COOLDOWN, default=cooldown
                    ): seconds,
                }
            ),
        )
