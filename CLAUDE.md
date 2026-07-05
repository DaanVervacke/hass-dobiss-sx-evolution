# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

- Install dev deps: `pip install -e ".[dev]"` (Python ≥3.12, HA target `2026.6.0`).
- Run tests: `pytest`
- Single test: `pytest tests/test_coordinator.py::test_name -x`
- CI runs `hacs/action` (integration) and `home-assistant/actions/hassfest` on push/PR — no local lint config, but ruff/mypy caches exist so keep code passing default ruff and mypy.

## Architecture

Custom Home Assistant integration in `custom_components/dobiss_sx_evolution/`. It bridges a DOBISS SX Evolution CAN bus (Max200 controller, 125 kbit/s) to HA `light` and `cover` entities. Purely `local_push` — no polling.

Data flow, one direction per layer:

- `protocol.py` — pure functions: parse/build CAN frames, BCD output encoding, brightness scaling (RX 0–90, TX 0–144 step 16). Stateless; the natural place for protocol tweaks.
- `controller.py` — owns the `python-can` bus (socketcand *or* slcan USB) in an executor. Runs a read loop that decodes frames via `protocol.parse_state_frame`, updates an in-memory `states: dict[OutputKey, int]` cache, and fans out to listeners via `async_add_listener`. Handles reconnect with exponential backoff (1s → 60s) and raises a HA repair issue when backoff saturates. On connect it sends a state-dump request and waits up to `DISCOVERY_TIMEOUT_S`.
- `coordinator.py` — `DataUpdateCoordinator` with `update_interval=None`. Reads module subentries from the config entry, builds `lights` / `dimmers` / `shutters` lists, constructs the controller, and forwards controller pushes into `async_set_updated_data`. `_async_update_data` only returns the cached dict; it raises `UpdateFailed` if the bus is down. `runtime_data` on the config entry *is* the coordinator (`type DobissConfigEntry = ConfigEntry[DobissCoordinator]`).
- `entity.py` / `light.py` / `cover.py` — entities subscribe to coordinator updates and translate CAN state to HA state. Shutters are open-loop: `assumed_state=True`, `is_closed` unknown (no position reporting from the bus).
- `__init__.py` — sets up coordinator, registers the hub device (Max200) as `SERVICE`, registers one device per module subentry with `via_device` pointing at the hub, forwards platforms, and registers the domain-wide `dobiss_sx_evolution.refresh` service (only once, removed when the last entry unloads).
- `config_flow.py` — connection step chooses socketcand vs USB (slcan). Modules are **subentries** (`SUBENTRY_TYPE_MODULE`), each with a letter A-Z, optional friendly name, a `dimmable` flag, and an `outputs` dict keyed by output number storing `{type: "light"|"shutter", ...}`. Adding/removing outputs is done via the module subentry's Reconfigure flow. An update listener reloads the entry on any subentry change so platforms recreate entities.
- `diagnostics.py` — redacts host in diagnostics dump.

Key domain constraints:
- Every output number is claimed at most once per module; shutter up/down must be distinct and both unclaimed.
- Module letter is a single ASCII char; used as-is in CAN frames.
- The controller's `states` dict is the single source of truth; entities never talk to the bus directly for reads.

## Repo-specific gotchas

- **Do not bump `VERSION` or add `async_migrate_entry`** — pre-release, config entry version is pinned at 1. See `memory/feedback_no_config_entry_version_bumps.md`.
- `manifest.json` declares `dependencies: ["usb"]` (needed for hassfest since USB discovery is used).
- `python-can`'s socketcand client busy-loops ~10s on a closed port; `controller.make_bus_sync` does a 2s TCP pre-check to fail fast — keep it.
- Brightness scaling is asymmetric on purpose (RX 0–90 echo, TX 0–144 step 16). Any change needs to hold `test_protocol.py`'s round-trip assertions.
