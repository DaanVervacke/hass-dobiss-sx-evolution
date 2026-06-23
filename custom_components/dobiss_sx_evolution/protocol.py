"""DOBISS SX Evolution CAN protocol - frame encoding and brightness scaling."""

from __future__ import annotations

from dataclasses import dataclass

from .const import (
    BRIGHTNESS_STEP,
    CAN_ID_STATE_DUMP,
    CAN_ID_TX_STATE,
    MAX_CAN_BRIGHTNESS_RX,
)


@dataclass(frozen=True)
class StateUpdate:
    """A parsed state update from the CAN bus."""

    module: str
    output: int  # 1-indexed
    state: int  # 0 = off; 1..MAX_CAN_BRIGHTNESS_RX for dimmable echo


def parse_state_frame(data: bytes) -> StateUpdate | None:
    """Parse an inbound state frame.

    Layout: <padding> <module ASCII> <0-indexed output> <state> <…>
    """
    if len(data) < 4:
        return None
    module_byte = data[1]
    output_zero = data[2]
    state_val = data[3]
    try:
        module = bytes([module_byte]).decode("ascii")
    except UnicodeDecodeError:
        return None
    return StateUpdate(module=module, output=output_zero + 1, state=state_val)


def build_state_frame(module: str, output: int, state: int) -> tuple[int, bytes] | None:
    """Build a (can_id, payload) tuple for a state write.

    DOBISS expects BCD encoding for the output byte:
    zero-indexed output 10 → 0x10 (not 0x0A), 11 → 0x11, etc.
    """
    if len(module) != 1:
        return None
    try:
        module_byte = module.encode("ascii")[0]
    except UnicodeEncodeError:
        return None
    zero = output - 1
    output_byte = (zero // 10) * 16 + (zero % 10)
    return CAN_ID_TX_STATE, bytes([0x00, module_byte, output_byte, state & 0xFF])


DUMP_REQUEST_FRAME: tuple[int, bytes] = (CAN_ID_STATE_DUMP, b"")


def can_to_ha_brightness(can_state: int) -> int:
    """Convert CAN echo brightness (0–90) to HA brightness (0–255)."""
    return can_state * 255 // MAX_CAN_BRIGHTNESS_RX


def ha_to_can_brightness(ha_brightness: int) -> int:
    """Convert HA brightness (0–255) to CAN write brightness (0–144, step 16)."""
    steps = round(ha_brightness * 9 / 255)
    return steps * BRIGHTNESS_STEP
