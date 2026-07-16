# Improvement Plans

## Audit history

| Run | Commit | Date | Depth | Plans |
|-----|--------|------|-------|-------|
| 1 | `155980f` | 2026-07-15 | deep | 001-010 |
| 2 | `b7ed404` | 2026-07-15 | deep | 012-020 |
| 3 | `f43da6c` | 2026-07-16 | deep | 021-029 |
| 4 | `47219e1` | 2026-07-16 | deep | 030-043 |
| 5 | `2b722bf` | 2026-07-16 | deep | 044-047 |

## Execution order

### Run 1 plans (001-010)

All landed. See status table below.

### Run 2 plans (012-020)

The recommended order respects dependencies and front-loads correctness fixes.

#### Tier 1 -- Correctness (do first)

1. **[012](012-fix-modules-list-on-output-reload.md)** -- Fix ctrl.modules not recomputed on output-only reload
2. **[013](013-validation-and-guards-batch.md)** -- Output upper bound + refresh guard + subentry unique_id fix

#### Tier 2 -- Stability

3. **[014](014-stabilize-entity-unique-id.md)** -- Remove module letter from entity unique_id for rename stability

#### Tier 3 -- Test coverage

4. **[017](017-quick-win-tests.md)** -- Quick-win tests: helpers, service, availability, error paths
5. **[018](018-subentry-flow-tests.md)** -- ModuleSubentryFlowHandler tests (all 6 methods)
6. **[019](019-usb-config-flow-tests.md)** -- USB config flow tests

#### Tier 4 -- Tech debt

7. **[015](015-cleanup-batch.md)** -- Dead constant, dimmable list->set, is_dataclass guard
8. **[016](016-code-quality-batch.md)** -- Double frame parse, connection_type constant

#### Tier 5 -- Direction

9. **[020](020-config-flow-reconfigure-direction.md)** -- Add async_step_reconfigure for connection changes

## Dependencies (Run 2)

```
012 ──── independent (P1, do first)
013 ──── independent (P1, do first)
014 ──── independent (breaks existing unique_ids)
015 ──── independent
016 ──── independent
017 ──── independent (benefits from 013 landing first)
018 ──── independent (benefits from 013 landing first)
019 ──── independent
020 ──── independent (benefits from 019 for USB test fixtures)
```

No hard blockers between plans.

## Status

| # | Slug | Status | Priority | Effort |
|---|------|--------|----------|--------|
| 001 | fix-dimmer-brightness-overflow | DONE | P1 | S |
| 002 | widen-entity-error-handlers | DONE | P1 | S |
| 003 | clamp-low-brightness-to-minimum-step | DONE | P1 | S |
| 004 | widen-coordinator-setup-exception-catch | DONE | P1 | S |
| 005 | fix-async-shutdown-bus-leak | DONE | P1 | S |
| 006 | add-ci-test-runner | DONE | P2 | S |
| 007 | fix-diagnostics-redaction | DONE | P2 | S |
| 008 | add-async-remove-config-entry-device | DONE | P2 | S |
| 009 | extract-output-list-builder | DONE | P3 | S |
| 010 | deps-hygiene-batch | DONE | P3 | M |
| 011 | *(superseded by commit 9ba2891)* | DONE | -- | -- |
| 012 | fix-modules-list-on-output-reload | DONE | P1 | S |
| 013 | validation-and-guards-batch | DONE | P1 | S |
| 014 | stabilize-entity-unique-id | DONE | P2 | M |
| 015 | cleanup-batch | DONE | P3 | S |
| 016 | code-quality-batch | DONE | P3 | S |
| 017 | quick-win-tests | DONE | P2 | S |
| 018 | subentry-flow-tests | DONE | P2 | M |
| 019 | usb-config-flow-tests | DONE | P2 | S |
| 020 | config-flow-reconfigure-direction | DONE | P3 | M |
| 021 | fix-setup-bus-leak | DONE | P1 | S |
| 022 | test-optimistic-brightness | DONE | P1 | M |
| 023 | test-cover-happy-path | DONE | P2 | S |
| 024 | batch-entity-setup | DONE | P2 | S |
| 025 | dedup-config-flow-probe | DONE | P2 | M |
| 026 | add-read-loop-watchdog | DONE | P2 | M |
| 027 | bump-test-harness | DONE | P2 | S |
| 028 | complete-reload-listener-test | DONE | P2 | S |
| 029 | housekeeping-batch | DONE | P3 | S |
| 030 | filter-inbound-can-frames | DONE | P1 | S |
| 031 | regenerate-translations | DONE | P1 | S |
| 032 | bump-python-can | DONE | P2 | S |
| 033 | tighten-validate-module-ascii | DONE | P1 | S |
| 034 | test-controller-commands | DONE | P2 | S |
| 035 | add-ruff-mypy-config | DONE | P2 | S |
| 036 | extract-connection-factory | DONE | P2 | S |
| 037 | extract-bus-error-context-manager | DONE | P2 | S |
| 038 | config-flow-test-gaps | DONE | P2 | S |
| 039 | add-refresh-service-schema | DONE | P2 | S |
| 040 | remove-unused-dev-deps | REJECTED | P3 | S |
| 041 | refresh-unload-test-gaps | DONE | P2 | S |
| 042 | coalesce-coordinator-broadcast | DONE | P2 | M |
| 043 | diagnostic-sensor-entities | DONE | P2 | S |
| 044 | sync-pyproject-version | MOOT | P3 | S |
| 045 | fix-shutdown-bus-handle-leak | DONE | P2 | S |
| 046 | test-reconnect-notifier-lifecycle | DONE | P2 | M |
| 047 | assert-state-byte-range | DONE | P3 | S |
| 048 | add-switch-platform | DONE | P2 | M |
| 049 | align-ci-with-engie-be | DONE | P2 | S |
| 050 | readme-overhaul | DONE | P2 | S |

### Run 3 plans (021-029)

#### Tier 1 -- Correctness (do first)

1. **[021](021-fix-setup-bus-leak.md)** -- Fix bus+notifier leak on partial setup failure
2. **[026](026-add-read-loop-watchdog.md)** -- Add liveness watchdog to CAN read loop

#### Tier 2 -- Test coverage

3. **[022](022-test-optimistic-brightness.md)** -- Test optimistic brightness state path
4. **[023](023-test-cover-happy-path.md)** -- Cover happy-path and availability tests
5. **[028](028-complete-reload-listener-test.md)** -- Complete reload listener test assertions

#### Tier 3 -- Performance & tech debt

6. **[024](024-batch-entity-setup.md)** -- Batch async_add_entities per subentry
7. **[025](025-dedup-config-flow-probe.md)** -- Extract shared probe-and-commit helper

#### Tier 4 -- Dependencies & docs

8. **[027](027-bump-test-harness.md)** -- Bump test harness to match manifest HA minimum
9. **[029](029-housekeeping-batch.md)** -- Housekeeping: CLAUDE.md, docstring, pyserial

## Dependencies (Run 3)

```
021 ──── independent (P1, do first)
022 ──── independent
023 ──── independent
024 ──── independent
025 ──── independent
026 ──── independent (P2, touches controller.py)
027 ──── independent (may cause test API changes)
028 ──── independent
029 ──── independent
```

No hard blockers between Run 3 plans.

### Run 4 plans (030-043)


#### Tier 1 -- Correctness (do first)

1. **[030](030-filter-inbound-can-frames.md)** -- Filter inbound CAN frames on RX arbitration ID
2. **[031](031-regenerate-translations.md)** -- Regenerate stale en.json from strings.json
3. **[033](033-tighten-validate-module-ascii.md)** -- Tighten module letter validation with isascii()

#### Tier 2 -- Dependencies

4. **[032](032-bump-python-can.md)** -- Bump python-can 4.5.0 to 4.6.1
5. **[040](040-remove-unused-dev-deps.md)** -- Remove unused dev deps (aiousbwatcher, serialx)

#### Tier 3 -- Best practices & hardening

6. **[039](039-add-refresh-service-schema.md)** -- Add vol.Schema({}) to refresh service registration
7. **[036](036-extract-connection-factory.md)** -- Extract from_config classmethods on connection dataclasses
8. **[037](037-extract-bus-error-context-manager.md)** -- Extract _bus_call() context manager in DobissEntity

#### Tier 4 -- Test coverage

9. **[034](034-test-controller-commands.md)** -- Test controller turn_on/off and shutter commands
10. **[038](038-config-flow-test-gaps.md)** -- Fill config flow test gaps batch
11. **[041](041-refresh-unload-test-gaps.md)** -- Test refresh service and unload edge cases

#### Tier 5 -- DX & tooling

12. **[035](035-add-ruff-mypy-config.md)** -- Add ruff/mypy config to pyproject.toml, ruff format to CI

#### Tier 6 -- Performance

13. **[042](042-coalesce-coordinator-broadcast.md)** -- Coalesce coordinator broadcast during frame bursts

#### Tier 7 -- Direction

14. **[043](043-diagnostic-sensor-entities.md)** -- Expose bus status and reconnect count as diagnostic entities

## Dependencies (Run 4)

```
030 ──── independent (P1, do first)
031 ──── independent (P1, do first)
032 ──── independent
033 ──── independent
034 ──── independent
035 ──── independent
036 ──── independent
037 ──── independent
038 ──── independent
039 ──── independent
040 ──── independent
041 ──── independent
042 ──── depends on 034 (controller command tests as safety net)
043 ──── independent
```

## Considered and rejected

| Finding | Reason |
|---------|--------|
| `always_update=False` identity check | Modern HA core uses `!=` equality, not `is` identity. Not a bug. |
| `_collect_initial_state` not called after reconnect | By design: would block for up to 15s during reconnect. State catches up via normal frame flow. |
| `is_bus_connected` window during backoff | `_bus` is still set during backoff sleep. The window is only during the sleep itself and not actionable. |
| Stop shutter sends only up_output=0 | By design per protocol docstring: setting up_output to 0 is the CAN stop command. |
| `_read_frames` busy-loop on socketcand | Known python-can limitation (socketcand client), mitigated by 2s TCP pre-check. Not actionable at integration level. |
| Entity state after CAN send not optimistic enough | Trade-off: CAN echo confirms the state within ms; optimistic state risks showing a state the bus rejected. Current approach is correct. |
| Run 4: `_probe_bus_sync` no try/finally on bus close | Bus construction failure means bus was never opened; no close needed. The except already logs. |
| Run 4: USB `list_ports` import not lazy | Import is at module level in config_flow; only runs when flow is active, not on integration load. Acceptable. |
| Run 4: Diagnostics missing `async_get_config_entry_diagnostics` | Already present at diagnostics.py:20. Subagent misread. |
| Run 4: Missing `iot_class` in manifest | Already present as `local_push`. Subagent misread. |
| Run 4: `_build_usb_device_options` sequential executor calls | Two awaits are for different USB APIs (list_ports, get_serial_by_id). Cannot be parallelized meaningfully. |
| Run 4: Cover entity missing `device_class` | Intentional: DOBISS shutters are generic covers, not specifically blinds/curtains/shades. No single device_class fits. |
| Run 4: No rate limit on CAN send commands | CAN bus inherently rate-limits via arbitration. Software rate limiting would add latency with no benefit. |
| Run 4: `_apply_local` does not validate brightness range | Range is enforced at protocol.py build_state_frame level; double-checking in controller would be redundant. |
| Run 4: Missing `suggested_display_precision` on brightness | Brightness is integer percentage 0-100; default precision is correct. |
| Run 4: No connection timeout on socketcand | Already handled by 2s TCP pre-check in SocketcandConnection.make_bus. |
| Run 4: D1 plugin system for output types | Over-engineered for 2 output types (light, shutter). YAGNI. |
| Run 4: D3 config backup/restore service | HA already handles config entry backup natively. |

### Run 5 plans (044-047)

#### Tier 1 -- Correctness

1. **[045](045-fix-shutdown-bus-handle-leak.md)** -- Fix CAN bus handle leak when shutdown races with reconnect

#### Tier 2 -- Test coverage

2. **[046](046-test-reconnect-notifier-lifecycle.md)** -- Test notifier teardown/setup lifecycle across reconnect

#### Tier 3 -- DX & hardening

3. **[044](044-sync-pyproject-version.md)** -- Sync pyproject.toml version with manifest.json
4. **[047](047-assert-state-byte-range.md)** -- Assert state byte range in build_state_frame

## Dependencies (Run 5)

```
044 ──── independent
045 ──── independent (P2, do first)
046 ──── independent
047 ──── independent
```

No hard blockers between Run 5 plans.

## Considered and rejected (Run 5)

| Finding | Reason |
|---------|--------|
| `_collect_initial_state` not exercising listeners | By design: coordinator listener is registered after `_collect_initial_state` returns; bulk state is pushed via `async_set_updated_data`. |
| `_flush_state` running after `async_shutdown` | Harmless: reads from a dict (safe) and pushes to coordinator (safe). Entities are being unloaded anyway. |
| D1: USB VID/PID matchers for auto-discovery | CAN adapters are generic devices, not DOBISS-specific. High false-positive risk on non-DOBISS setups. |
| D2: Cover position tracking via timing | L effort, accuracy degrades with friction/load. Most users just want open/close/stop. |
