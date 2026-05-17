# HOTO Smart Weight — Home Assistant integration

A custom [Home Assistant](https://www.home-assistant.io/) integration for the
**HOTO Smart Weight** kitchen scale, installable via [HACS](https://hacs.xyz/).

Unlike most Xiaomi-ecosystem BLE appliances, this scale does **not** broadcast its
readings in advertisements. The integration therefore actively connects to the scale,
runs the Xiaomi "mible" secure-login handshake, and subscribes to the encrypted weight
characteristic.

## Features

- Single live-updating **Weight** sensor (grams), with `connected` and `stabilized`
  state attributes.
- **Auto-connect** — the scale is picked up automatically whenever it advertises.
- **Idle disconnect** — after the weight has been constant for a configurable number
  of seconds (default 60), the integration disconnects so the scale can power down and
  save battery. Set the timeout to `0` to stay connected permanently.
- **Reconnect cooldown** — the scale keeps advertising for a while after a disconnect,
  so after an idle disconnect the integration ignores it for a configurable cooldown
  (default 30 s) before reconnecting; otherwise it would reconnect immediately and the
  scale would never sleep. Set the cooldown longer than the scale's post-disconnect
  advertising window. Set it to `0` to reconnect as soon as the scale is seen.

## Requirements

- Home Assistant 2024.12 or newer with a working Bluetooth adapter (or an
  active/connectable Bluetooth proxy).
- The scale's **per-device token** — a 12-byte (24 hex character) secret. You must
  obtain this yourself (e.g. extracted from the Xiaomi/HOTO app's local storage); the
  integration does not retrieve it.

## Installation

1. Add this repository to HACS as a custom repository (category: *Integration*), or
   copy `custom_components/hoto_ble/` into your Home Assistant `config/custom_components/`
   directory.
2. Restart Home Assistant.
3. Go to **Settings → Devices & Services → Add Integration → HOTO Smart Weight**. If the
   scale was discovered automatically it appears as a notification; otherwise add it
   manually with its Bluetooth MAC address.
4. Enter the device token when prompted.

## Configuration

The idle-disconnect timeout can be changed any time via the integration's
**Configure** button (**Options**).

## Project layout

- `custom_components/hoto_ble/` — the integration. `hoto.py` is a self-contained,
  Home-Assistant-agnostic BLE driver and can be run standalone for hardware testing:
  `python hoto.py <MAC> <TOKEN_HEX>` (from inside the component directory).
