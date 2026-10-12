# ConnectLife API → Home Assistant mapping (repository audit, 2026-10-12)

This is a **code-level** inventory, not a capture from a physical unit. Parser declarations and Android strings are **not proof** that a particular Hisense model publishes a value. Entities must be gated on actual device status/properties and capabilities.

| API/property | Implemented HA surface | Evidence and limit |
| --- | --- | --- |
| `GET /clife-svc/pu/get_device_status_list` → device `statusList` | Coordinator refresh for all supported devices, default 30 s; per-device climate, sensor, switch, select, number, humidifier and water-heater platforms | `const.py`, `api.py:async_get_devices`, `coordinator.py`. Presence in response must be checked per model. |
| `GET /clife-svc/get_property_list` | Parser capability filtering, conditional entity creation | `api.py:_async_get_cached_property_list`; property availability is not necessarily a live measurement. |
| `POST /clife-svc/pu/query_static_data` | Static flags (including `f_humidity`, `Power_function`) used to gate selected entities | Static absence is not an affirmative claim of unsupported capability. |
| `POST /device/pu/property/set` | Entity controls via `async_control_device` | `coordinator.py`; a successful write does not guarantee immediate state confirmation. |
| `/msg/registerPhoneDevice`, `/msg/get_msg_and_channels` and WebSocket status notifications | Push status changes (`status_devicestatus`) and online state (`status_wifistatus`) | `websocket.py`, `coordinator.py`; periodic REST fallback retained. |
| `f_temp_in` / legacy `t_temp_in` | `climate.current_temperature`; standalone `sensor.indoor_temperature` when reported | Climate and sensor read actual status, no invented temperature. |
| `f_humidity` | `sensor.indoor_humidity` when actual field reported and static flag does not explicitly deny support | Value is percent; absence remains unknown. |
| `f_votage`, `f_electricity`, `f_power_display` | Voltage, current and reported power sensors | These are different from the calculated `f_estimated_power`; units/quality depend on device firmware. |
| `f_estimated_power` | Estimated power and confidence diagnostics | Computed using voltage/current/cloud energy; **not** a metered instantaneous power reading. |
| `/clife-svc/pu/get_hour_power` → `f_power_consumption` | Energy consumption sensor | Calculated from cloud hourly values; **validate accumulation/reset behavior and units on real devices** before Energy Dashboard use. |
| `/basic/self_check/info` | Fault diagnostics exposed by supported entity definitions | `api.py:async_api_self_check`, `sensor.py`; avoid enabling every error flag for devices that never report them. |

## Proven code issues and addressed safeguards

- WebSocket previously dropped **every** message arriving within 1 second of the previous one. Drop removed.
- WebSocket URL previously logged embedded access token. Log removed.
- REST debug previously logged authorization-related headers and full response bodies. Generic header-name and body-length logging reduces disclosure.
- Unsupported parser no longer aborts discovery of other devices; missing static capability dictionaries no longer cause the observed `.get()`/membership errors.
- DRY mode (`3` for supported split parser) no longer suppresses environmental sensor availability. Standalone room temperature recognizes both `f_temp_in` and `t_temp_in` where present; no value is fabricated.

## Still requiring real-device evidence

- Whether each installed unit reports humidity while powered off or in DRY, and whether those readings remain fresh.
- Whether `f_power_display` is authoritative instantaneous watts for each model; whether energy value is cumulative across days or daily-resetting.
- WebSocket real-world event coverage, loss rates, refresh after writes and cloud throttling. Keep 30-second REST fallback pending measurements; adaptive polling without metrics could silently stale sensors.
- Additional API strings found in an Android application must be correlated with a reachable endpoint, relevant device feature code and an authentic payload before new HA entities are justified.

No production Home Assistant instance was changed or restarted during this audit.

## Read-only authenticated diagnostics added 2026-10-12

`custom_components/hisense_ac_plugin/diagnostics.py` implements the standard
Home Assistant `async_get_config_entry_diagnostics` hook. This accesses the
existing in-memory coordinator/OAuth session without exporting the token.
Default capture does not make network calls; its status and capability data
are **real HA cached API data only when the new integration version is loaded**.
No such live dump was available through the current @HAS connector. The
optional hourly energy and self-check calls require explicit opt-in and remain
read-only. See `docs/live_api_capture.md` for precise capture instructions.

Coordinator timestamps distinguish the last REST refresh and last processed
WebSocket event, but do not claim individual status properties were updated
at either time. A bounded event log stores only notification type, field
names and time, without raw messages or device identifiers.

## First real HA diagnostic capture — 2026-10-12 14:03:55 UTC

The user-provided native HA diagnostics contains three anonymized online
devices of type 009 / feature 128. All report t_power=0. Two have
t_work_mode=0, one has t_work_mode=3, which is DRY retained while OFF,
not proof of active dehumidification.

| API field | Cached values (three devices) | Conclusion |
| --- | --- | --- |
| f_temp_in | 25 / 27 / 24 | Present even when powered off |
| f_humidity | absent / absent / absent | No actual humidity measurement established |
| f_e_inhumidity | 0 / 0 / 0 | Fault flag, not humidity |
| f_power_display | absent / absent / absent | Declared in parser, not observed as live power |
| f_electricity | 0 / 0 / 0 | Current reading while off |
| f_votage | 1 / 1 / 1 | Scale/meaning not validated; should not present as literal 1 V |
| f_power_consumption | 0 / 0 / 0.03 | Cached energy, may be daily-resetting |
| f_estimated_power | 0 / 0 / 0 | Calculated locally, not hardware measured |

The last REST success was about 15 seconds before capture. There were
no recorded WebSocket events or last WS timestamp, which does not by
itself establish a disconnected socket. Parser attributes were present
but raw API property lists and static maps were empty. More snapshots
during normal OFF/DRY/COOL/HEAT operation are needed to confirm
per-property freshness, voltage scale and event coverage.
