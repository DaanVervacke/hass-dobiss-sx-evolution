# DOBISS SX Evolution Home Assistant integration

[![HACS Custom][hacsbadge]][hacs]
[![GitHub Release][releasebadge]][release]
[![Quality Scale][qualitybadge]][quality]
[![License][licensebadge]](LICENSE)

Custom [Home Assistant](https://www.home-assistant.io/) integration for the
[DOBISS SX Evolution](https://dobiss.com/). Connects to a DOBISS CAN bus via a
[`socketcand`](https://github.com/linux-can/socketcand) daemon over TCP and
exposes configured outputs as `light` and `cover` entities.

## Supported devices

DOBISS SX Evolution installations driven by a Max200 controller, reachable
via a `socketcand` bridge on the CAN bus.

## Supported functions

- `light` entities for on/off and dimmable outputs
- `cover` entities for shutter outputs (open, close, stop)
- `dobiss_sx_evolution.refresh` service to trigger a manual state dump

## How state updates work

Push-based via the CAN bus, no polling. The integration sends a state-dump
request on startup and after every reconnect, then listens for incoming frames
and pushes updates to entities in real time. Reconnection uses exponential
backoff and a Home Assistant repair issue is raised when the connection is
persistently unavailable.

## Prerequisites

- Home Assistant **2026.6.0** or newer
- A [`socketcand`](https://github.com/linux-can/socketcand) daemon reachable
  from your Home Assistant instance, with the DOBISS CAN bus attached to the
  interface you want to expose (default: `can0`, port `29536`)

## Installation

### HACS (recommended)

This integration is not yet in the default HACS store. Add it as a custom
repository first:

1. Open HACS in your Home Assistant instance.
2. Click the three dots in the top right corner and select **Custom repositories**.
3. Add `https://github.com/DaanVervacke/hass-dobiss-sx-evolution` with category **Integration**.
4. Search for **DOBISS SX Evolution** in HACS and click **Download**.
5. Restart Home Assistant.
6. Go to **Settings** > **Devices & Services** > **Add Integration** and search for **DOBISS SX Evolution**.

### Manual

Copy `custom_components/dobiss_sx_evolution/` into your Home Assistant
`config/custom_components/` directory and restart.

## Configuration

Configuration is done entirely through the Home Assistant UI.

### Connection setup

Go to **Settings** > **Devices & Services** > **Add Integration** and search
for **DOBISS SX Evolution**.

| Field | Default | Description |
|---|---|---|
| Host | -- | Hostname or IP address of the `socketcand` daemon |
| Port | `29536` | TCP port the daemon listens on |
| Remote CAN interface | `can0` | CAN interface name as configured in `socketcand` |

The integration probes the connection before saving. If the probe fails, a
`cannot_connect` error is shown and no entry is created.

### Adding modules

After the connection entry is saved, add one **Module** subentry per physical
DOBISS module in your installation. Open the DOBISS SX Evolution card in
**Settings** > **Devices & Services** and click **Add module**.

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
- **Remove output**: select an existing output to remove
- **Edit module**: change the module letter, friendly name, or dimmable flag

Each output number can only be claimed once per module. For shutters, the
up and down output numbers must be distinct and unclaimed.

### Reconfiguring the connection

If the `socketcand` host, port, or interface changes, open the DOBISS SX
Evolution card in **Settings** > **Devices & Services** and use the
**Reconfigure** option. The form pre-fills the current values. The entry is
reloaded automatically on success.

## Services

### `dobiss_sx_evolution.refresh`

Sends a state-dump request to every active DOBISS entry. Use this to
resynchronise entity states without restarting Home Assistant.

This service takes no parameters.

## Known limitations

- Shutter position is not reported by the CAN bus. `is_closed` is always
  unknown and `assumed_state` is `True`, so open/close/stop buttons remain
  available regardless of the last known state.

## Troubleshooting

1. **Enable debug logging.** Open **Settings** > **Devices & Services**, click
   the three-dot menu on the DOBISS SX Evolution entry, and select **Enable
   debug logging**. Reproduce the issue, then choose **Disable debug logging**
   from the same menu. Home Assistant will offer to download the captured log.

2. **Download diagnostics.** From the same three-dot menu, choose **Download
   diagnostics**. The resulting JSON redacts your host address and is safe to
   attach to a GitHub issue.

3. **Common errors:**
   - *Cannot connect*: the `socketcand` daemon is unreachable at the
     configured host and port. Check that the daemon is running and that
     network access from Home Assistant is not blocked.
   - *Cannot send*: a CAN frame write failed after the connection was
     established. The integration will attempt to reconnect automatically.
   - *Repair issue "cannot_connect"*: the CAN bus has been unreachable long
     enough for backoff to reach its ceiling. Resolve the network or daemon
     issue, the repair issue clears automatically on reconnection.

4. **File an issue** at
   [github.com/DaanVervacke/hass-dobiss-sx-evolution/issues](https://github.com/DaanVervacke/hass-dobiss-sx-evolution/issues)
   and include the diagnostics JSON and the relevant log lines.

## Removing the integration

1. Go to **Settings** > **Devices & Services**.
2. Find the **DOBISS SX Evolution** card and click the three-dot menu.
3. Select **Delete**.

No changes are made to your DOBISS hardware or `socketcand` daemon.

## Use cases

- Control your lights and dimmers from Home Assistant automations and
  dashboards.
- Integrate your shutters into scenes and time-based automations.
- Use the refresh service to resync state after a `socketcand` daemon restart.

## License

[MIT](LICENSE) - Daan Vervacke ([@DaanVervacke](https://github.com/DaanVervacke))

[hacs]: https://github.com/hacs/integration
[hacsbadge]: https://img.shields.io/badge/HACS-Custom-orange.svg
[release]: https://github.com/DaanVervacke/hass-dobiss-sx-evolution/releases
[releasebadge]: https://img.shields.io/github/v/release/DaanVervacke/hass-dobiss-sx-evolution
[quality]: https://www.home-assistant.io/docs/quality_scale/
[qualitybadge]: https://img.shields.io/badge/quality_scale-gold-gold.svg
[licensebadge]: https://img.shields.io/github/license/DaanVervacke/hass-dobiss-sx-evolution
