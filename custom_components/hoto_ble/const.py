"""Constants for the HOTO Smart Weight scale integration."""

from __future__ import annotations

DOMAIN = "hoto_ble"

# Config / option keys
CONF_TOKEN = "token"
CONF_IDLE_TIMEOUT = "idle_timeout"
CONF_RECONNECT_COOLDOWN = "reconnect_cooldown"

# Seconds the weight reading may stay constant before we drop the BLE
# connection so the scale can power down / sleep. Reconnect happens
# automatically the next time the scale advertises (i.e. when it is woken).
DEFAULT_IDLE_TIMEOUT = 60

# Seconds to ignore advertisements after an idle disconnect, so the scale is
# left alone long enough to actually go to sleep. Must be longer than the
# scale's post-disconnect advertising window. 0 disables the cooldown.
DEFAULT_RECONNECT_COOLDOWN = 30

# GATT characteristic UUIDs, copied verbatim from the working proof-of-concept
# (hoto_client_poc.py). Do not "fix" these strings: the scale's firmware is
# matched against them as-is.
WEIGHT_UUID = "0000010-2006-56c6-22e7-46f696d2e696d"
UPNP_UUID = "00000010-0000-1000-8000-00805f9b34fb"
AVDTP_UUID = "00000019-0000-1000-8000-00805f9b34fb"

# HKDF parameters for the Xiaomi "mible" secure-login handshake.
MIBLE_LOGIN_INFO = b"mible-login-info"

MANUFACTURER = "HOTO"
MODEL = "Smart Weight"
