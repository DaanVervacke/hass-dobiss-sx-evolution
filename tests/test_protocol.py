"""Tests for protocol.py pure functions."""

from __future__ import annotations

from datetime import datetime

import pytest

from custom_components.dobiss_sx_evolution.protocol import (
    DUMP_REQUEST_FRAME,
    MOOD_ADDRESS_BYTE,
    StateUpdate,
    build_clock_set_packets,
    build_config_download_intro,
    build_mood_frame,
    build_output_name_intro,
    build_state_frame,
    can_to_ha_brightness,
    can_tx_to_rx,
    ha_to_can_brightness,
    output_name_eeprom_addr,
    parse_config_response,
    parse_output_name,
    parse_state_frame,
    to_bcd,
)

# ---------------------------------------------------------------------------
# parse_state_frame
# ---------------------------------------------------------------------------


def test_parse_state_frame_valid_basic():
    """Module 'A', zero-indexed output 0 → 1-indexed output 1."""
    result = parse_state_frame(b"\x00A\x00\x01")
    assert result == StateUpdate(module="A", output=1, state=1)


def test_parse_state_frame_valid_various():
    """Zero-indexed output 5 → 1-indexed 6; full-brightness state 0xFF."""
    result = parse_state_frame(b"\x00A\x05\xff")
    assert result is not None
    assert result.module == "A"
    assert result.output == 6  # 0-indexed 5 → 1-indexed 6
    assert result.state == 0xFF


def test_parse_state_frame_various_modules():
    """Any printable ASCII letter is accepted as the module byte."""
    for letter in ("B", "C", "Z"):
        result = parse_state_frame(bytes([0x00, ord(letter), 0x02, 0x00]))
        assert result is not None
        assert result.module == letter
        assert result.output == 3  # zero-indexed 2 → 1-indexed 3
        assert result.state == 0


def test_parse_state_frame_too_short_returns_none():
    """Payloads shorter than 4 bytes must return None."""
    assert parse_state_frame(b"") is None
    assert parse_state_frame(b"\x00A\x00") is None
    assert parse_state_frame(b"\x00A") is None


def test_parse_state_frame_non_ascii_module_returns_none():
    """A non-ASCII module byte (e.g. 0xFF) must return None."""
    assert parse_state_frame(b"\x00\xff\x00\x01") is None
    assert parse_state_frame(b"\x00\x80\x00\x00") is None


def test_parse_state_frame_bcd_output_decoding():
    """Output bytes are BCD-decoded on inbound frames, mirroring build_state_frame.

    Regression: an earlier version did a plain output_byte + 1, which routed
    inbound frames for outputs 11 and 12 (arriving on the wire as 0x10 / 0x11
    under BCD) to phantom outputs 17 and 18.  Their entities then stayed off
    because the states cache was never populated under the correct key.
    """
    cases = [
        (0x00, 1),
        (0x08, 9),
        (0x09, 10),
        (0x10, 11),
        (0x11, 12),
    ]
    for byte, expected_output in cases:
        result = parse_state_frame(bytes([0x00, ord("A"), byte, 0x00]))
        assert result is not None, f"byte=0x{byte:02X} returned None"
        assert result.output == expected_output, (
            f"byte=0x{byte:02X}: got output={result.output}, expected {expected_output}"
        )


def test_parse_and_build_state_frame_roundtrip():
    """Every output 1..12 must survive an encode/decode roundtrip.

    Guarantees the transmit and receive paths agree on the output number so
    that state broadcasts DOBISS echoes for a write we sent land on the
    same cache key the entity reads.
    """
    for output in range(1, 13):
        built = build_state_frame("A", output, 0)
        assert built is not None
        _, payload = built
        # Rebuild an inbound frame using the same output-byte encoding.
        inbound = bytes([0x00, ord("A"), payload[2], 0x00])
        parsed = parse_state_frame(inbound)
        assert parsed is not None
        assert parsed.output == output, (
            f"roundtrip broke for output {output}: got {parsed.output}"
        )


# ---------------------------------------------------------------------------
# build_state_frame
# ---------------------------------------------------------------------------


def test_build_state_frame_can_id():
    """The returned CAN ID must be 0x800102."""
    result = build_state_frame("A", 1, 0xFF)
    assert result is not None
    can_id, _ = result
    assert can_id == 0x800102


def test_build_state_frame_bcd_output_encoding():
    """Output bytes use BCD (pseudo-decimal) encoding, not hex.

    The function converts to 0-indexed first (output - 1), then encodes:
      zero = output - 1
      output_byte = (zero // 10) * 16 + (zero % 10)

    Selected spot-checks:
      output 1  → zero 0  → 0x00
      output 9  → zero 8  → 0x08  (NOT 0x09)
      output 10 → zero 9  → 0x09  (NOT 0x0A / NOT 0x10)
      output 11 → zero 10 → 0x10  (BCD tens digit kicks in)
      output 12 → zero 11 → 0x11
    """
    cases = [
        (1, 0x00),
        (9, 0x08),
        (10, 0x09),
        (11, 0x10),
        (12, 0x11),
    ]
    for output, expected_byte in cases:
        result = build_state_frame("A", output, 0xFF)
        assert result is not None, f"output={output} returned None"
        _, payload = result
        assert payload[2] == expected_byte, (
            f"output={output}: got 0x{payload[2]:02X}, expected 0x{expected_byte:02X}"
        )


def test_build_state_frame_rejects_state_wider_than_byte():
    """State values outside 0-255 must return None."""
    assert build_state_frame("A", 1, 256) is None
    assert build_state_frame("A", 1, 0x144) is None
    assert build_state_frame("A", 1, -1) is None


def test_build_state_frame_accepts_boundary_state_values():
    """State values 0 and 255 are valid and must not be rejected."""
    result_0 = build_state_frame("A", 1, 0)
    assert result_0 is not None
    assert result_0[1][3] == 0

    result_255 = build_state_frame("A", 1, 255)
    assert result_255 is not None
    assert result_255[1][3] == 255


def test_build_state_frame_payload_structure():
    """Payload is always 4 bytes: [0x00, module_ascii, output_bcd, state]."""
    result = build_state_frame("B", 3, 0x7F)
    assert result is not None
    _, payload = result
    assert len(payload) == 4
    assert payload[0] == 0x00
    assert payload[1] == ord("B")
    assert payload[3] == 0x7F


def test_build_state_frame_invalid_module_returns_none():
    """Multi-char or empty module strings must return None."""
    assert build_state_frame("", 1, 0) is None
    assert build_state_frame("AB", 1, 0) is None
    assert build_state_frame("ABC", 1, 0) is None


# ---------------------------------------------------------------------------
# build_dump_request
# ---------------------------------------------------------------------------


def test_dump_request_frame():
    """Must be exactly (0x800101, b'')."""
    can_id, payload = DUMP_REQUEST_FRAME
    assert can_id == 0x800101
    assert payload == b""


# ---------------------------------------------------------------------------
# can_to_ha_brightness
# ---------------------------------------------------------------------------


def test_can_to_ha_brightness():
    """CAN range 0-90 maps to HA range 0-255 (integer division)."""
    assert can_to_ha_brightness(0) == 0
    assert can_to_ha_brightness(90) == 255
    # 45 * 255 // 90 = 11475 // 90 = 127
    assert can_to_ha_brightness(45) == 127


def test_can_to_ha_brightness_clamps_to_255():
    """Out-of-range CAN values must clamp to 255, not overflow."""
    assert can_to_ha_brightness(144) == 255
    assert can_to_ha_brightness(90) == 255
    assert can_to_ha_brightness(100) == 255


# ---------------------------------------------------------------------------
# ha_to_can_brightness
# ---------------------------------------------------------------------------


def test_ha_to_can_brightness_boundary_values():
    """HA 0→0, HA 255→144 (max), step of 16 confirmed."""
    assert ha_to_can_brightness(0) == 0
    assert ha_to_can_brightness(255) == 144


def test_ha_to_can_brightness_midpoint():
    """HA 128 → round(128*9/255)=5 steps → 5*16=80."""
    assert ha_to_can_brightness(128) == 80


def test_ha_to_can_brightness_near_zero_clamps_to_minimum_step():
    """HA 1 rounds to 0 steps, clamped to the min step so turn_on never sends OFF."""
    assert ha_to_can_brightness(1) == 16


def test_ha_to_can_brightness_step_of_16():
    """Every result is a multiple of 16."""
    for ha in range(256):
        result = ha_to_can_brightness(ha)
        assert result % 16 == 0, f"ha={ha} gave {result}, not a multiple of 16"


def test_ha_to_can_brightness_zero_stays_zero():
    """HA 0 (off) must stay 0, not be clamped up."""
    assert ha_to_can_brightness(0) == 0


def test_ha_to_can_brightness_positive_never_zero():
    """Any positive HA brightness must map to at least the minimum CAN step."""
    for ha in range(1, 256):
        result = ha_to_can_brightness(ha)
        assert result >= 16, f"ha={ha} gave {result}, expected >= 16"


def test_can_tx_to_rx_zero():
    assert can_tx_to_rx(0) == 0


def test_can_tx_to_rx_all_dimmer_steps():
    """Every valid TX step (multiples of 16) maps to an exact RX value."""
    expected = [
        (16, 10),
        (32, 20),
        (48, 30),
        (64, 40),
        (80, 50),
        (96, 60),
        (112, 70),
        (128, 80),
        (144, 90),
    ]
    for tx, rx in expected:
        assert can_tx_to_rx(tx) == rx, f"can_tx_to_rx({tx}) should be {rx}"


def test_can_tx_to_rx_max():
    assert can_tx_to_rx(144) == 90


# ---------------------------------------------------------------------------
# to_bcd
# ---------------------------------------------------------------------------


def test_to_bcd_basic_values():
    """to_bcd matches the ConversieVars lookup table from MaxTool."""
    assert to_bcd(0) == 0x00
    assert to_bcd(9) == 0x09
    assert to_bcd(10) == 0x10
    assert to_bcd(11) == 0x11
    assert to_bcd(59) == 0x59
    assert to_bcd(99) == 0x99


def test_to_bcd_matches_inline_expression():
    """to_bcd(n) must equal the original inline (n // 10) * 16 + (n % 10)."""
    for n in range(100):
        assert to_bcd(n) == (n // 10) * 16 + (n % 10)


def test_to_bcd_rejects_negative():
    with pytest.raises(ValueError, match="BCD value out of range"):
        to_bcd(-1)


def test_to_bcd_rejects_100():
    with pytest.raises(ValueError, match="BCD value out of range"):
        to_bcd(100)


def test_to_bcd_accepts_boundary_values():
    assert to_bcd(0) == 0x00
    assert to_bcd(99) == 0x99


# ---------------------------------------------------------------------------
# build_clock_set_packets
# ---------------------------------------------------------------------------


def test_build_clock_set_packets_sizes():
    """Intro is 16 bytes, output is 7 bytes."""
    dt = datetime(2026, 7, 17, 14, 30, 45)
    intro, output = build_clock_set_packets(dt)
    assert len(intro) == 16
    assert len(output) == 7


def test_build_clock_set_packets_intro_header():
    """Intro starts with 0xED, 0x4B ('K'), 0x30 ('0')."""
    dt = datetime(2026, 1, 1, 0, 0, 0)
    intro, _ = build_clock_set_packets(dt)
    assert intro[0] == 0xED
    assert intro[1] == 0x4B
    assert intro[2] == 0x30


def test_build_clock_set_packets_bcd_values():
    """Output bytes are BCD-encoded date/time fields."""
    dt = datetime(2026, 7, 17, 14, 30, 45)
    _, output = build_clock_set_packets(dt)
    assert output[0] == to_bcd(45)  # second
    assert output[1] == to_bcd(30)  # minute
    assert output[2] == to_bcd(14)  # hour
    assert output[3] == to_bcd(dt.isoweekday())  # dow (Friday = 5)
    assert output[4] == to_bcd(17)  # day
    assert output[5] == to_bcd(7)  # month
    assert output[6] == to_bcd(26)  # year % 100


# ---------------------------------------------------------------------------
# build_config_download_intro
# ---------------------------------------------------------------------------


def test_build_config_download_intro_structure():
    """Intro is 16 bytes: 0xED, 'a', '0', 0xA0, rest zero."""
    intro = build_config_download_intro()
    assert len(intro) == 16
    assert intro[0] == 0xED
    assert intro[1] == 0x61
    assert intro[2] == 0x30
    assert intro[3] == 0xA0
    assert intro[4:] == bytes(12)


# ---------------------------------------------------------------------------
# parse_config_response
# ---------------------------------------------------------------------------


def test_parse_config_response_active_modules():
    """Active module slots with ASCII letters are returned."""
    data = bytearray(36)
    data[0] = ord("A")
    data[2] = ord("C")
    result = parse_config_response(bytes(data))
    assert result == [("A", 0), ("C", 2)]


def test_parse_config_response_skips_zero_slots():
    """Zero-filled slots are not returned."""
    data = bytes(36)
    assert parse_config_response(data) == []


def test_parse_config_response_skips_non_alpha():
    """Non-alpha bytes (digits, control chars) are skipped."""
    data = bytearray(36)
    data[0] = 0xFF
    data[1] = ord("3")
    data[2] = 0x01
    assert parse_config_response(bytes(data)) == []


def test_parse_config_response_lowercase_uppercased():
    """Lowercase letters are uppercased."""
    data = bytearray(36)
    data[5] = ord("b")
    result = parse_config_response(bytes(data))
    assert result == [("B", 5)]


def test_parse_config_response_short_data_returns_empty():
    """Data shorter than the module count is rejected instead of raising."""
    assert parse_config_response(b"") == []
    assert parse_config_response(b"\x41" * 5) == []


# ---------------------------------------------------------------------------
# build_output_name_intro
# ---------------------------------------------------------------------------


def test_build_output_name_intro_structure():
    """Intro has correct header and EEPROM address."""
    intro = build_output_name_intro(0, 0)
    assert len(intro) == 16
    assert intro[0] == 0xED
    assert intro[1] == 0x75
    assert intro[2] == 0x31
    assert intro[3] == 0xA0
    # addr = 128 + 0*384 + 0*32 = 128 = 0x0080
    assert intro[4] == 0x00
    assert intro[5] == 0x80


def test_build_output_name_intro_address_calculation():
    """Address = 128 + module_index*384 + output_index*32."""
    intro = build_output_name_intro(2, 5)
    addr = 128 + 2 * 384 + 5 * 32
    assert intro[4] == addr >> 8
    assert intro[5] == addr & 0xFF


# ---------------------------------------------------------------------------
# parse_output_name
# ---------------------------------------------------------------------------


def test_parse_output_name_valid():
    """A configured output returns its trimmed name."""
    data = bytearray(32)
    name = b"Kitchen ceiling"
    data[: len(name)] = name
    assert parse_output_name(bytes(data)) == "Kitchen ceiling"


def test_parse_output_name_unconfigured():
    """Byte 1 == 0xFF means unconfigured, returns None."""
    data = bytearray(32)
    data[1] = 0xFF
    assert parse_output_name(bytes(data)) is None


def test_parse_output_name_too_short():
    """Data shorter than 32 bytes returns None."""
    assert parse_output_name(b"\x00" * 10) is None


def test_parse_output_name_empty_name():
    """All-zero name (after stripping) returns None."""
    data = bytes(32)
    assert parse_output_name(data) is None


# ---------------------------------------------------------------------------
# build_mood_frame
# ---------------------------------------------------------------------------


def test_build_mood_frame_basic():
    """Correct CAN ID and payload for a mood activation."""
    result = build_mood_frame(5)
    assert result is not None
    can_id, payload = result
    assert can_id == 0x800102
    assert len(payload) == 4
    assert payload[0] == 0x00
    assert payload[1] == MOOD_ADDRESS_BYTE
    assert payload[2] == 5
    assert payload[3] == 0x01


def test_build_mood_frame_no_bcd():
    """Mood number is sent as raw byte, not BCD encoded.

    Address 0x53 is a special address that skips ConversieVars in the
    MaxTool IL. mood_number=15 should stay 0x0F, not become 0x15 (BCD).
    """
    result = build_mood_frame(15)
    assert result is not None
    _, payload = result
    assert payload[2] == 0x0F


def test_build_mood_frame_boundary_values():
    """Mood numbers 0 and 99 are both valid."""
    assert build_mood_frame(0) is not None
    assert build_mood_frame(99) is not None


def test_build_mood_frame_out_of_range():
    """Mood numbers outside 0-99 must return None."""
    assert build_mood_frame(-1) is None
    assert build_mood_frame(100) is None
    assert build_mood_frame(255) is None


# ---------------------------------------------------------------------------
# output_name_eeprom_addr
# ---------------------------------------------------------------------------


def test_output_name_eeprom_addr_first_slot():
    assert output_name_eeprom_addr(0, 0) == 128


def test_output_name_eeprom_addr_formula():
    assert output_name_eeprom_addr(2, 5) == 128 + 2 * 384 + 5 * 32


def test_output_name_eeprom_addr_matches_intro():
    """Address from the helper must match the intro builder."""
    for m in range(3):
        for o in range(12):
            addr = output_name_eeprom_addr(m, o)
            intro = build_output_name_intro(m, o)
            assert intro[4] == addr >> 8
            assert intro[5] == addr & 0xFF
