"""DOBISS SX Evolution CAN protocol - frame encoding and brightness scaling."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .const import (
    BRIGHTNESS_STEP,
    CAN_ID_STATE_DUMP,
    CAN_ID_TX_STATE,
    MAX_CAN_BRIGHTNESS_RX,
    MAX_CAN_BRIGHTNESS_TX,
)


@dataclass(frozen=True)
class StateUpdate:
    """A parsed state update from the CAN bus."""

    module: str
    output: int  # 1-indexed
    state: int  # 0 = off; 1..MAX_CAN_BRIGHTNESS_RX for dimmable echo


def to_bcd(value: int) -> int:
    """Encode a decimal value (0-99) as BCD (ConversieVars.To from MaxTool)."""
    if not 0 <= value <= 99:
        raise ValueError(f"BCD value out of range: {value}")
    return (value // 10) * 16 + (value % 10)


def build_clock_set_packets(dt: datetime) -> tuple[bytes, bytes]:
    """Return (intro, output) for a K0 clock-set command.

    Field order TBD from live testing. Starting with standard RTC order.
    """
    intro = bytearray(16)
    intro[0] = 0xED
    intro[1] = 0x4B  # 'K'
    intro[2] = 0x30  # '0'

    output = bytes(
        [
            to_bcd(dt.second),
            to_bcd(dt.minute),
            to_bcd(dt.hour),
            to_bcd(dt.isoweekday()),
            to_bcd(dt.day),
            to_bcd(dt.month),
            to_bcd(dt.year % 100),
        ]
    )
    return bytes(intro), output


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
    output_byte = to_bcd(zero)
    if not 0 <= state <= 255:
        return None
    return CAN_ID_TX_STATE, bytes([0x00, module_byte, output_byte, state])


DUMP_REQUEST_FRAME: tuple[int, bytes] = (CAN_ID_STATE_DUMP, b"")

CONFIG_RESPONSE_SIZE = 36
OUTPUT_NAME_RESPONSE_SIZE = 32
_MODULES_PER_CONTROLLER = 18
OUTPUTS_PER_MODULE = 12


def build_config_download_intro() -> bytes:
    """Build the 16-byte intro for a ConfigVars (a0) download request."""
    intro = bytearray(16)
    intro[0] = 0xED
    intro[1] = 0x61  # 'a'
    intro[2] = 0x30  # '0'
    intro[3] = 0xA0
    return bytes(intro)


def parse_config_response(data: bytes) -> list[tuple[str, int]]:
    """Parse a 36-byte ConfigVars response into active modules.

    Returns a list of (module_letter, module_index) for slots that contain
    a valid ASCII letter.  module_index is the slot position (0-17), needed
    for EEPROM address calculation when fetching output names.
    """
    if len(data) < _MODULES_PER_CONTROLLER:
        return []
    result: list[tuple[str, int]] = []
    for i in range(_MODULES_PER_CONTROLLER):
        char = data[i]
        if char == 0:
            continue
        try:
            letter = bytes([char]).decode("ascii")
        except UnicodeDecodeError:
            continue
        if letter.isalpha():
            result.append((letter.upper(), i))
    return result


def output_name_eeprom_addr(module_index: int, output_index: int) -> int:
    """Calculate EEPROM address for a UitgangVars (u1) record."""
    return 128 + module_index * 384 + output_index * 32


def build_output_name_intro(module_index: int, output_index: int) -> bytes:
    """Build the 16-byte intro for a UitgangVars (u1) download request."""
    addr = output_name_eeprom_addr(module_index, output_index)
    intro = bytearray(16)
    intro[0] = 0xED
    intro[1] = 0x75  # 'u'
    intro[2] = 0x31  # '1'
    intro[3] = 0xA0
    intro[4] = addr >> 8
    intro[5] = addr & 0xFF
    return bytes(intro)


def parse_output_name(data: bytes) -> str | None:
    """Parse a 32-byte UitgangVars response into an output name.

    Returns None if the output is unconfigured (byte 1 == 0xFF).
    """
    if len(data) < OUTPUT_NAME_RESPONSE_SIZE:
        return None
    if data[1] == 0xFF:
        return None
    name = bytes(data[:31]).decode("ascii", errors="replace").strip("\x00").strip()
    return name or None


MOOD_ADDRESS_BYTE = 0x53


def build_mood_frame(mood_number: int) -> tuple[int, bytes] | None:
    """Build a (can_id, payload) tuple for a mood activation.

    Address byte 0x53 ('S') routes to the mood subsystem. The mood number
    and action are sent as raw bytes (special addresses skip BCD encoding).
    """
    if not 0 <= mood_number <= 99:
        return None
    return CAN_ID_TX_STATE, bytes([0x00, MOOD_ADDRESS_BYTE, mood_number, 0x01])


def can_to_ha_brightness(can_state: int) -> int:
    """Convert CAN echo brightness (0-90) to HA brightness (0-255)."""
    return min(can_state * 255 // MAX_CAN_BRIGHTNESS_RX, 255)


def ha_to_can_brightness(ha_brightness: int) -> int:
    """Convert HA brightness (0-255) to CAN write brightness (0-144, step 16)."""
    if ha_brightness <= 0:
        return 0
    steps = round(ha_brightness * 9 / 255)
    return max(1, steps) * BRIGHTNESS_STEP


def can_tx_to_rx(tx_value: int) -> int:
    """Convert CAN TX brightness (0-144) to expected RX echo value (0-90)."""
    if tx_value <= 0:
        return 0
    return round(tx_value * MAX_CAN_BRIGHTNESS_RX / MAX_CAN_BRIGHTNESS_TX)
