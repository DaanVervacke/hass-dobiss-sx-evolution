"""Tests for protocol.py pure functions."""
from __future__ import annotations

from custom_components.dobiss_sx_evolution.protocol import (
    DUMP_REQUEST_FRAME,
    StateUpdate,
    build_state_frame,
    can_to_ha_brightness,
    ha_to_can_brightness,
    parse_state_frame,
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
    result = parse_state_frame(b"\x00A\x05\xFF")
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
    assert parse_state_frame(b"\x00\xFF\x00\x01") is None
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
    """CAN range 0–90 maps to HA range 0–255 (integer division)."""
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
    """HA 1 rounds to 0 steps, but is clamped to the minimum step (16) so turn_on never sends OFF."""
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
