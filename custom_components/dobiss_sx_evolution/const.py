"""Constants for the DOBISS SX Evolution integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "dobiss_sx_evolution"

# Connection type constants
CONNECTION_TYPE_SOCKETCAND: Final = "socketcand"
CONNECTION_TYPE_USB: Final = "usb"

# socketcand connection constants
CONF_INTERFACE: Final = "interface"
CONF_HOST: Final = "host"
CONF_PORT: Final = "port"

# USB connection constants
CONF_DEVICE: Final = "device"

# Subentry field constants
CONF_MODULE: Final = "module"
CONF_NAME: Final = "name"

# Subentry type identifiers
SUBENTRY_TYPE_MODULE: Final = "module"

DEFAULT_INTERFACE: Final = "can0"
DEFAULT_PORT: Final = 29536
DEFAULT_BAUDRATE: Final = 115200

# CAN frame identifiers (29-bit extended). Values match the DOBISS firmware
# expectations one-to-one with the Gleam reference implementation.
CAN_ID_TX_STATE: Final = 0x800102  # 8_388_866 - HA → DOBISS state write
CAN_ID_STATE_DUMP: Final = 0x800101  # 8_388_865 - request full state dump

OUTPUTS_PER_MODULE: Final = 12
DISCOVERY_TIMEOUT_S: Final = 15.0

# Brightness scaling: DOBISS echoes 0–90, accepts 0–144 in steps of 16.
MAX_CAN_BRIGHTNESS_TX: Final = 144
MAX_CAN_BRIGHTNESS_RX: Final = 90
BRIGHTNESS_STEP: Final = 16
