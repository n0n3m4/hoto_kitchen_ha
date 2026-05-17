"""Self-contained BLE protocol driver for the HOTO Smart Weight scale.

This module is intentionally free of any Home Assistant imports so it can be
run and tested standalone (see ``__main__`` at the bottom). It refactors
``hoto_client_poc.py`` into an async, push-based ``HotoScale`` class that:

* connects to the scale and runs the Xiaomi "mible" secure-login handshake,
* subscribes to the encrypted weight characteristic and decrypts readings,
* disconnects after the weight has been constant for ``idle_timeout`` seconds
  so the scale can power down, then
* reconnects automatically the next time the scale advertises (woken by the
  user). The owner is responsible for calling :meth:`signal_available` from a
  BLE advertisement callback to drive that reconnect.
"""

from __future__ import annotations

import asyncio
import logging
import os
import struct
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from time import monotonic

from bleak import BleakClient
from bleak.backends.device import BLEDevice
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives import hmac as crypto_hmac
from cryptography.hazmat.primitives.ciphers.aead import AESCCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

try:  # package import (inside Home Assistant)
    from .const import AVDTP_UUID, MIBLE_LOGIN_INFO, UPNP_UUID, WEIGHT_UUID
except ImportError:  # standalone import (running this file directly for testing)
    from const import AVDTP_UUID, MIBLE_LOGIN_INFO, UPNP_UUID, WEIGHT_UUID

_LOGGER = logging.getLogger(__name__)

# Backoff bounds for failed connection attempts.
_INITIAL_BACKOFF = 3.0
_MAX_BACKOFF = 60.0
# How often the idle watchdog checks the constant-weight timer.
_WATCHDOG_INTERVAL = 1.0


@dataclass(frozen=True)
class HotoScaleState:
    """Immutable snapshot of the scale's state, pushed to listeners."""

    connected: bool = False
    weight: float | None = None
    stabilized: bool = False


def parse_weight(payload: bytes) -> float:
    """Decode a decrypted weight payload into grams.

    Byte layout (from the proof-of-concept): bytes 5-6 are a little-endian
    16-bit raw value at 0.1 g resolution; byte 7 bit 0x10 is the sign.
    """
    raw = payload[5] | (payload[6] << 8)
    weight = raw / 10.0
    if payload[7] & 0x10:
        weight = -weight
    return weight


def derive_session_keys(
    token: bytes, my_random: bytes, remote_random: bytes
) -> tuple[bytes, bytes, bytes, bytes]:
    """Derive ``(dev_key, app_key, dev_iv, app_iv)`` via the mible HKDF."""
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=40,
        salt=my_random + remote_random,
        info=MIBLE_LOGIN_INFO,
    )
    derived = hkdf.derive(token)
    return derived[0:16], derived[16:32], derived[32:36], derived[36:40]


def calculate_hmac(key: bytes, salt: bytes) -> bytes:
    """Compute HMAC-SHA256(key, salt)."""
    h = crypto_hmac.HMAC(key, hashes.SHA256())
    h.update(salt)
    return h.finalize()


class HotoScaleError(Exception):
    """Raised when the handshake or connection fails."""


@dataclass
class _Channel:
    """A GATT notification channel backed by an asyncio queue."""

    queue: asyncio.Queue[bytes] = field(default_factory=asyncio.Queue)

    def handler(self, _sender: object, data: bytearray) -> None:
        """Bleak notification callback (runs on the event loop thread)."""
        self.queue.put_nowait(bytes(data))

    async def get(self, prefix: bytes = b"", timeout: float = 10.0) -> bytes:
        """Await the next frame, optionally requiring a prefix."""
        msg = await asyncio.wait_for(self.queue.get(), timeout)
        if prefix and not msg.startswith(prefix):
            raise HotoScaleError(
                f"Unexpected frame {msg.hex()}, expected prefix {prefix.hex()}"
            )
        return msg


class HotoScale:
    """Manages a single HOTO scale: connection lifecycle and weight stream."""

    def __init__(
        self,
        address: str,
        token: bytes,
        idle_timeout: float | None = None,
        reconnect_cooldown: float = 0.0,
    ) -> None:
        """Initialise the driver. ``token`` is the per-device secret bytes."""
        self.address = address
        self._token = token
        self._idle_timeout = idle_timeout
        self._reconnect_cooldown = reconnect_cooldown
        self.state = HotoScaleState()

        self._callbacks: list[Callable[[HotoScaleState], None]] = []
        self._ble_device: BLEDevice | None = None

        self._loop_task: asyncio.Task[None] | None = None
        self._stopped = False
        # Gates a (re)connection attempt. Set initially so the first connect
        # proceeds; thereafter only an advertisement re-opens the gate.
        self._available = asyncio.Event()
        self._available.set()
        self._disconnect = asyncio.Event()
        self._backoff = _INITIAL_BACKOFF

        # Idle tracking: timestamp of the last *changed* weight value.
        self._last_change = monotonic()
        # True while the in-progress/just-ended session was ended by the idle
        # watchdog (as opposed to an unexpected drop). monotonic() deadline
        # before which advertisements must not trigger a reconnect.
        self._idle_triggered = False
        self._cooldown_until = 0.0

    # ------------------------------------------------------------------ API

    def register_callback(
        self, callback: Callable[[HotoScaleState], None]
    ) -> Callable[[], None]:
        """Register a state listener; returns an unsubscribe function."""
        self._callbacks.append(callback)

        def _unsub() -> None:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

        return _unsub

    def set_idle_timeout(self, seconds: float | None) -> None:
        """Update the constant-weight idle timeout at runtime."""
        self._idle_timeout = seconds

    def set_reconnect_cooldown(self, seconds: float) -> None:
        """Update the post-idle-disconnect reconnect cooldown at runtime."""
        self._reconnect_cooldown = seconds

    def signal_available(self, ble_device: BLEDevice | None = None) -> None:
        """Tell the driver the scale is in range (call from an advert callback).

        This is what reconnects the scale after a disconnect: when the user
        wakes the scale it advertises again, and this opens the gate. After an
        idle disconnect the gate stays shut until the reconnect cooldown has
        elapsed, so the scale is left alone long enough to go to sleep. The
        freshest BLEDevice is captured regardless, for the eventual reconnect.
        """
        if ble_device is not None:
            self._ble_device = ble_device
        if monotonic() >= self._cooldown_until:
            self._available.set()

    async def async_start(self) -> None:
        """Start the background connect/reconnect loop."""
        if self._loop_task is None:
            self._stopped = False
            self._loop_task = asyncio.get_running_loop().create_task(self._run())

    async def async_stop(self) -> None:
        """Stop the driver and disconnect cleanly."""
        self._stopped = True
        self._available.set()
        self._disconnect.set()
        if self._loop_task is not None:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            self._loop_task = None

    # -------------------------------------------------------------- internal

    def _emit(self, **changes: object) -> None:
        """Update state and notify all listeners."""
        self.state = replace(self.state, **changes)
        for callback in list(self._callbacks):
            try:
                callback(self.state)
            except Exception:  # noqa: BLE001 - never let a listener break us
                _LOGGER.exception("HOTO %s: state listener failed", self.address)

    async def _run(self) -> None:
        """Connect, serve readings, disconnect on idle, then wait to reconnect."""
        while not self._stopped:
            await self._available.wait()
            if self._stopped:
                break
            try:
                await self._connect_and_listen()
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("HOTO %s: session ended: %s", self.address, err)
                # Failed connect: keep the gate open but back off.
                await asyncio.sleep(self._backoff)
                self._backoff = min(self._backoff * 2, _MAX_BACKOFF)
                continue
            finally:
                if self.state.connected:
                    self._emit(connected=False)
            # Clean session end (idle disconnect or device gone): close the
            # gate and wait for the next advertisement to reopen it. After an
            # idle disconnect, also arm the cooldown first so advertisements
            # during the cooldown window cannot slip the gate back open.
            if self._idle_triggered:
                self._idle_triggered = False
                self._cooldown_until = monotonic() + self._reconnect_cooldown
            self._available.clear()

    async def _connect_and_listen(self) -> None:
        """One full session: connect, log in, stream weight until disconnect."""
        self._disconnect.clear()
        # Clear any stale idle flag so only this session's idle watchdog can
        # arm the reconnect cooldown.
        self._idle_triggered = False
        device = self._ble_device
        if device is None:
            raise HotoScaleError(
                f"No BLEDevice known for {self.address} yet; waiting for advert"
            )

        client = await establish_connection(
            BleakClientWithServiceCache,
            device,
            self.address,
            disconnected_callback=lambda _c: self._disconnect.set(),
        )
        _LOGGER.debug("HOTO %s: connected, starting handshake", self.address)
        try:
            dev_key, dev_iv = await self._login(client)
            self._backoff = _INITIAL_BACKOFF
            self._last_change = monotonic()
            self._emit(connected=True)

            await client.start_notify(
                WEIGHT_UUID, self._make_weight_handler(dev_key, dev_iv)
            )
            watchdog = asyncio.ensure_future(self._idle_watchdog())
            try:
                await self._disconnect.wait()
            finally:
                watchdog.cancel()
        finally:
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                _LOGGER.debug("HOTO %s: error on disconnect", self.address)

    async def _login(self, client: BleakClient) -> tuple[bytes, bytes]:
        """Run the mible secure-login handshake; return ``(dev_key, dev_iv)``.

        Ported step-for-step from ``hoto_client_poc.py``.
        """
        upnp = _Channel()
        avdtp = _Channel()
        await client.start_notify(UPNP_UUID, upnp.handler)
        await client.start_notify(AVDTP_UUID, avdtp.handler)

        async def send(uuid: str, data: bytes) -> None:
            await client.write_gatt_char(uuid, data)

        async def send_miparcel(uuid: str, data: bytes) -> None:
            # MiParcel framing for a single small (<=18 byte) frame.
            await client.write_gatt_char(uuid, b"\x01\x00" + data)

        # Step 0: undocumented wake/sync sequence captured from a packet dump.
        await send(UPNP_UUID, b"\xa4")
        frame = await avdtp.get()
        await send(AVDTP_UUID, frame[:2] + bytes([frame[2] + 1]) + frame[3:])
        frame = await avdtp.get()
        await send(AVDTP_UUID, frame[:2] + bytes([frame[2] + 1]) + frame[3:])
        await asyncio.sleep(0.2)

        # Steps 1-3: request login, send key, await RCV_RDY.
        await send(UPNP_UUID, b"\x24\x00\x00\x00")
        await send(AVDTP_UUID, b"\x00\x00\x00\x0b\x01\x00")
        await avdtp.get(b"\x00\x00\x01\x01")

        # Steps 4-6: exchange random nonces.
        my_random = os.urandom(16)
        await send_miparcel(AVDTP_UUID, my_random)
        await avdtp.get(b"\x00\x00\x01\x00")
        remote_random_frame = await avdtp.get()
        remote_random = remote_random_frame[4:]
        if len(remote_random) != 16:
            raise HotoScaleError(
                f"Bad remote_random length: {len(remote_random)}"
            )
        await send(AVDTP_UUID, b"\x00\x00\x03\x00")

        # Step 7: receive the device's HMAC (not verified yet, see below).
        remote_info_frame = await avdtp.get()
        remote_info_hmac = remote_info_frame[2:]
        await send(AVDTP_UUID, b"\x00\x00\x03\x00")

        # Step 8: derive session keys.
        dev_key, app_key, dev_iv, _app_iv = derive_session_keys(
            self._token, my_random, remote_random
        )
        expected = calculate_hmac(dev_key, remote_random + my_random)
        if remote_info_hmac and remote_info_hmac != expected:
            # Non-fatal for now: the POC never verified this. A mismatch most
            # likely means a wrong token; surface it but let the device decide.
            _LOGGER.warning(
                "HOTO %s: device HMAC mismatch (token may be wrong)",
                self.address,
            )

        # Steps 9-12: send our HMAC, await RCV_OK and LOGIN_OK.
        await send(AVDTP_UUID, b"\x00\x00\x00\x0a\x01\x00")
        await avdtp.get(b"\x00\x00\x01\x01")
        client_info_hmac = calculate_hmac(app_key, my_random + remote_random)
        await send_miparcel(AVDTP_UUID, client_info_hmac)
        await avdtp.get(b"\x00\x00\x01\x00", timeout=5)
        await upnp.get(b"\x21\x00\x00\x00")
        _LOGGER.debug("HOTO %s: login complete", self.address)

        return dev_key, dev_iv

    def _make_weight_handler(
        self, dev_key: bytes, dev_iv: bytes
    ) -> Callable[[object, bytearray], None]:
        """Build the AES-CCM-decrypting notification handler for weight data."""
        aesccm = AESCCM(dev_key, tag_length=4)

        def _handler(_sender: object, data: bytearray) -> None:
            ctr = int.from_bytes(data[0:2], "little")
            nonce = dev_iv + b"\x00\x00\x00\x00" + struct.pack("<I", ctr)
            try:
                plaintext = aesccm.decrypt(nonce, bytes(data[2:]), None)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("HOTO %s: decrypt failed: %s", self.address, err)
                return
            # Payload byte 3: 8 = stabilized reading, 7 = live/unstable.
            if len(plaintext) < 8 or plaintext[3] not in (7, 8):
                return
            weight = parse_weight(plaintext)
            if self.state.weight != weight:
                self._last_change = monotonic()
            self._emit(weight=weight, stabilized=plaintext[3] == 8)

        return _handler

    async def _idle_watchdog(self) -> None:
        """Disconnect once the weight has been constant for ``idle_timeout``."""
        while True:
            await asyncio.sleep(_WATCHDOG_INTERVAL)
            if not self._idle_timeout:
                continue
            if monotonic() - self._last_change >= self._idle_timeout:
                _LOGGER.debug(
                    "HOTO %s: weight constant for %ss, disconnecting so the "
                    "scale can sleep",
                    self.address,
                    self._idle_timeout,
                )
                self._idle_triggered = True
                self._disconnect.set()
                return


if __name__ == "__main__":  # pragma: no cover - manual hardware test
    import sys

    from bleak import BleakScanner

    logging.basicConfig(level=logging.DEBUG)

    async def _main() -> None:
        address = sys.argv[1]
        token = bytes.fromhex(sys.argv[2])
        scale = HotoScale(address, token, idle_timeout=None)
        scale.register_callback(lambda s: print("STATE:", s))

        device = await BleakScanner.find_device_by_address(address, timeout=20)
        if device is None:
            print(f"Device {address} not found")
            return
        scale.signal_available(device)
        await scale.async_start()
        try:
            await asyncio.sleep(600)
        finally:
            await scale.async_stop()

    if len(sys.argv) != 3:
        print("Usage: python -m hoto <MAC> <TOKEN_HEX>")
        sys.exit(1)
    asyncio.run(_main())
