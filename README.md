<h1 align="center">DOBISS SX Evolution - Home Assistant integration</h1>

<p align="center">
  <a href="https://github.com/hacs/integration"><img src="https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=flat-square" alt="HACS Custom"></a>
  <a href="https://github.com/DaanVervacke/hass-dobiss-sx-evolution/releases"><img src="https://img.shields.io/github/v/release/DaanVervacke/hass-dobiss-sx-evolution?style=flat-square&label=version&sort=semver" alt="Latest release"></a>
  <a href="https://github.com/DaanVervacke/hass-dobiss-sx-evolution/actions/workflows/validate.yml"><img src="https://img.shields.io/github/actions/workflow/status/DaanVervacke/hass-dobiss-sx-evolution/validate.yml?style=flat-square&label=hacs%20%2F%20hassfest" alt="Validate"></a>
  <a href="https://github.com/DaanVervacke/hass-dobiss-sx-evolution/actions/workflows/test.yml"><img src="https://img.shields.io/github/actions/workflow/status/DaanVervacke/hass-dobiss-sx-evolution/test.yml?style=flat-square&label=tests" alt="Tests"></a>
  <a href="https://www.home-assistant.io/"><img src="https://img.shields.io/badge/dynamic/json?url=https://raw.githubusercontent.com/DaanVervacke/hass-dobiss-sx-evolution/main/hacs.json&query=$.homeassistant&label=Home%20Assistant&prefix=%3E%3D&color=41BDF5&logo=home-assistant&logoColor=white&style=flat-square" alt="Home Assistant"></a>
  <a href="https://www.home-assistant.io/docs/quality_scale/"><img src="https://img.shields.io/badge/quality_scale-gold-gold.svg?style=flat-square" alt="Quality Scale"></a>
  <a href="./LICENSE"><img src="https://img.shields.io/github/license/DaanVervacke/hass-dobiss-sx-evolution?style=flat-square" alt="License"></a>
</p>

---

Custom [Home Assistant](https://www.home-assistant.io/) integration for the
[DOBISS SX Evolution](https://dobiss.com/). Connects to a DOBISS CAN bus
(Max200 controller, 125 kbit/s) either via a
[`socketcand`](https://github.com/linux-can/socketcand) daemon over TCP or
via a USB CAN adapter (slcan-compatible, e.g. CANable) plugged directly into
your Home Assistant host, and exposes configured outputs as light, cover,
and switch entities.

## Table of contents

- [Features](#features)
- [Entities](#entities)
- [Prerequisites](#prerequisites)
- [Socketcand setup guide](#socketcand-setup-guide)
- [Installation](#installation)
- [Configuration](#configuration)
  - [Connection setup](#connection-setup)
  - [Adding modules](#adding-modules)
  - [Configuring outputs](#configuring-outputs)
  - [Reconfiguring the connection](#reconfiguring-the-connection)
- [Services](#services)
- [Known limitations](#known-limitations)
- [Removing the integration](#removing-the-integration)
- [Troubleshooting](#troubleshooting)
- [License](#license)

## Features

- Purely local, push-based updates via the CAN bus with no cloud dependency and no polling
- Light entities for on/off and dimmable outputs
- Cover entities for shutter outputs (open, close, stop)
- Switch entities for generic on/off relays (door buzzers, irrigation valves, ventilation fans)
- Diagnostic sensors for CAN bus connection status and reconnect count
- Automatic reconnection with exponential backoff and a Home Assistant repair issue when the bus stays down
- State-dump request on startup and after every reconnect to bring entities up to date
- Domain-wide `dobiss_sx_evolution.refresh` service for manual state resync
- Supports both socketcand (TCP) and USB CAN adapters (slcan)

## Entities

The integration creates the following entities for each configured output
or for the hub device itself.

### Lights

One `light` entity per configured light output. Brightness control is
available when the parent module is marked as a dimmer.

### Covers

One `cover` entity per configured shutter output. Each shutter uses two
physical outputs (up and down). Position is not reported by the bus, so
covers use `assumed_state` and `is_closed` is always unknown.

### Switches

One `switch` entity per configured switch output. Use this for generic
on/off relays that are not lights or shutters.

### Diagnostic sensors

Created automatically on the hub device (Max200 controller):

| Entity | Type | Description |
|---|---|---|
| CAN bus connected | Binary sensor | Whether the integration currently has an active CAN bus connection |
| CAN bus reconnections | Sensor | Number of times the integration has reconnected since the last Home Assistant restart |

## Prerequisites

- Home Assistant **2026.6.0** or newer
- One of:
  - A [`socketcand`](https://github.com/linux-can/socketcand) daemon
    reachable over TCP from Home Assistant (default: `can0`, port `29536`), or
  - A USB CAN adapter (slcan-compatible, e.g. CANable) plugged into the Home
    Assistant host and appearing as `/dev/ttyACM*`, `/dev/ttyUSB*`, or the
    Windows/macOS equivalent

See the [Socketcand setup guide](#socketcand-setup-guide) below if you have
not configured socketcand yet.

## Socketcand setup guide

`socketcand` acts as a TCP bridge between your CAN interface and network
clients like this integration.

### 1. Bring up the CAN interface

The Max200 operates at **125 kbit/s**.

```bash
sudo ip link set can0 type can bitrate 125000
sudo ip link set can0 up
```

Replace `can0` with your actual interface name if it differs. Verify the
interface is up with `ip link show can0`.

### 2. Start socketcand

```bash
socketcand -i can0 -p 29536
```

You can make this persistent with systemd.

## Installation

### HACS (recommended)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=DaanVervacke&repository=hass-dobiss-sx-evolution&category=integration)

Click the badge above to open this repository in HACS, then select
**Download**. After HACS finishes, restart Home Assistant.

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=dobiss_sx_evolution)

Click the badge above to open the **Add integration** dialog for DOBISS SX
Evolution. The connection form opens directly. See
[Configuration](#configuration) for what each field expects.

#### Manual steps

If the badges above do not work in your browser:

1. Open HACS in your Home Assistant instance.
2. Search for **DOBISS SX Evolution** in the HACS search bar and install it.
3. Restart Home Assistant.
4. Go to **Settings** > **Devices & services** > **Add integration** and search for **DOBISS SX Evolution**.

If the search returns no results, add this repository as a custom repository
first:

1. Open HACS, click the three dots in the top right corner, and select **Custom repositories**.
2. Add `https://github.com/DaanVervacke/hass-dobiss-sx-evolution` with category **Integration**.
3. Search for **DOBISS SX Evolution** in HACS and install it.

### Manual

Copy `custom_components/dobiss_sx_evolution/` into your Home Assistant
`config/custom_components/` directory and restart.

## Configuration

Configuration is done entirely through the Home Assistant UI.

### Connection setup

Go to **Settings** > **Devices & services** > **Add integration** and search
for **DOBISS SX Evolution**. Pick either **socketcand** or **USB CAN adapter**
on the first step.

**socketcand**

| Field | Default | Description |
|---|---|---|
| Host | -- | Hostname or IP address of the `socketcand` daemon |
| Port | `29536` | TCP port the daemon listens on |
| Remote CAN interface | `can0` | CAN interface name as configured in `socketcand` |

**USB CAN adapter**

| Field | Default | Description |
|---|---|---|
| Device | -- | Serial device path, picked from a dropdown of detected ports (e.g. `/dev/ttyACM0`, `/dev/serial/by-id/...`) |

The adapter is opened as an `slcan` bus at 115200 baud. The bus itself runs
at 125 kbit/s (the Max200 line speed).

The integration probes the connection before saving. If the probe fails, a
`cannot_connect` error is shown and no entry is created.

### Adding modules

After the connection entry is saved, add one **Module** subentry per physical
DOBISS module in your installation. Open the DOBISS SX Evolution card in
**Settings** > **Devices & services** and click **Add module**.

| Field | Required | Description |
|---|---|---|
| Module letter | Yes | Single letter A-Z identifying the module on the bus |
| Module name | No | Friendly name, defaults to `Module <letter>` |
| Dimmable | No | Enable for dimmer modules, applies brightness support to every light on this module |

### Configuring outputs

From each module's **Reconfigure** menu you can manage the outputs assigned
to that module:

- **Add light**: output number and an optional friendly name
- **Add shutter**: up-output number, down-output number and an optional friendly name
- **Add switch**: output number and an optional friendly name
- **Remove output**: select an existing output to remove
- **Edit module**: change the module letter, friendly name, or dimmable flag

Each output number can only be claimed once per module. For shutters, the
up and down output numbers must be distinct and unclaimed.

### Reconfiguring the connection

If the `socketcand` host/port/interface or the USB device path changes, open
the DOBISS SX Evolution card in **Settings** > **Devices & services** and use
the **Reconfigure** option. The form pre-fills the current values. The entry
is reloaded automatically on success.

## Services

### `dobiss_sx_evolution.refresh`

Sends a state-dump request to every active DOBISS entry. Use this to
resynchronize entity states without restarting Home Assistant.

This service takes no parameters.

## Known limitations

- Shutter position is not reported by the CAN bus. `is_closed` is always
  unknown and `assumed_state` is `True`, so open/close/stop buttons remain
  available regardless of the last known state.

## Removing the integration

1. Go to **Settings** > **Devices & services**.
2. Find the **DOBISS SX Evolution** card and click the three-dot menu.
3. Select **Delete**.

No changes are made to your DOBISS hardware or `socketcand` daemon.

## Troubleshooting

If the integration is misbehaving, work through these steps before filing an
issue:

1. **Enable debug logging.** Open **Settings** > **Devices & services**, click
   the three-dot menu on the DOBISS SX Evolution entry, and select **Enable
   debug logging**. Reproduce the issue, then choose **Disable debug logging**
   from the same menu. Home Assistant will offer to download the captured log.

2. **Download diagnostics.** From the same three-dot menu, choose **Download
   diagnostics**. The resulting JSON redacts your host address and is safe to
   attach to a GitHub issue.

3. **Common errors:**
   - *Cannot connect*: the `socketcand` daemon is unreachable at the
     configured host and port, **or** the USB CAN adapter cannot be opened.
     Check that the daemon is running (socketcand), or that the device path
     is correct and Home Assistant has permission to open it (USB).
   - *Cannot send*: a CAN frame write failed after the connection was
     established. The integration will attempt to reconnect automatically.
   - *Repair issue "cannot_connect"*: the CAN bus has been unreachable long
     enough for backoff to reach its ceiling. Resolve the network or daemon
     issue, the repair issue clears automatically on reconnection.

4. **File an issue** at
   [github.com/DaanVervacke/hass-dobiss-sx-evolution/issues](https://github.com/DaanVervacke/hass-dobiss-sx-evolution/issues)
   and include the diagnostics JSON and the relevant log lines.

## License

[MIT](LICENSE) - Daan Vervacke ([@DaanVervacke](https://github.com/DaanVervacke))
