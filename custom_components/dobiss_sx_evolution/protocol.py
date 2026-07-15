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

    Layout: <padding> <module ASCII> <BCD 0-indexed output> <state> <…>

    DOBISS uses BCD symmetrically on the output byte: outputs 11 and 12
    (zero-indexed 10 and 11) arrive as 0x10 and 0x11, matching the encoding
    build_state_frame uses on transmit.  A plain +1 decode would misroute
    those frames to outputs 17 and 18 and their entities would stay off.
    """
    if len(data) < 4:
        return None
    module_byte = data[1]
    output_byte = data[2]
    state_val = data[3]
    try:
        module = bytes([module_byte]).decode("ascii")
    except UnicodeDecodeError:
        return None
    zero = (output_byte >> 4) * 10 + (output_byte & 0x0F)
    return StateUpdate(module=module, output=zero + 1, state=state_val)


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
    return min(can_state * 255 // MAX_CAN_BRIGHTNESS_RX, 255)


def ha_to_can_brightness(ha_brightness: int) -> int:
    """Convert HA brightness (0–255) to CAN write brightness (0–144, step 16)."""
    if ha_brightness <= 0:
        return 0
    steps = round(ha_brightness * 9 / 255)
    return max(1, steps) * BRIGHTNESS_STEP
