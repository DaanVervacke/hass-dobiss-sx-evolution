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
CONF_MAX200_HOST: Final = "max200_host"

# Subentry field constants
CONF_MODULE: Final = "module"
CONF_NAME: Final = "name"

# Subentry type identifiers
SUBENTRY_TYPE_MODULE: Final = "module"
SUBENTRY_TYPE_MODULE_IMPORT: Final = "module_import"
SUBENTRY_TYPE_MOOD: Final = "mood"

DEFAULT_INTERFACE: Final = "can0"
DEFAULT_PORT: Final = 29536
DEFAULT_BAUDRATE: Final = 115200

# CAN frame identifiers (29-bit extended). Values match the DOBISS firmware
# expectations one-to-one with the Gleam reference implementation.
CAN_ID_TX_STATE: Final = 0x800102  # 8_388_866 - HA → DOBISS state write
CAN_ID_STATE_DUMP: Final = 0x800101  # 8_388_865 - request full state dump
CAN_ID_RX_STATE: Final = 0x100FF00  # 16_842_496 - DOBISS -> HA state broadcast

CONF_CONNECTION_TYPE: Final = "connection_type"

DISCOVERY_TIMEOUT_S: Final = 15.0

# Longest gap allowed between inbound frames while the read loop is running.
# On a healthy bus, DOBISS modules report state periodically even without
# user interaction, so this much silence indicates a dead link (e.g. the
# controller lost power but TCP/serial stayed up).
LIVENESS_TIMEOUT_S: Final = 300.0

# Brightness scaling: DOBISS echoes 0-90, accepts 0-144 in steps of 16.
MAX_CAN_BRIGHTNESS_TX: Final = 144
MAX_CAN_BRIGHTNESS_RX: Final = 90
BRIGHTNESS_STEP: Final = 16

MAX200_TCP_PORT: Final = 1001
CLOCK_SYNC_INTERVAL_HOURS: Final = 4
