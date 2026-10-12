# Collect authentic ConnectLife diagnostics using the existing HA OAuth session

## Production boundary

This repository contains `custom_components/hisense_ac_plugin/diagnostics.py`.
The file is a **Home Assistant native diagnostics platform**, not a standalone
login script. It gets the existing coordinator through
`hass.data["hisense_ac_plugin"][entry.entry_id]` and calls no login flow,
password API, token-export mechanism or climate control command.

The currently installed integration must be updated through the user's own
normal deployment/update procedure before this new diagnostics provider is
available in production. Do not copy files, restart HA or deploy merely to
run a read-only audit without the owner's normal maintenance decision.

After the updated integration is loaded, in the HA interface open **Settings →
Devices & services → Hisense AC → ⋮ → Download diagnostics**. HA creates a
JSON file using the already authenticated integration. No new ConnectLife
authorization is required. If the menu does not offer diagnostics, HA is still
running a version without this provider (or this HA version hides the action);
it is not evidence of OAuth failure. Share the JSON, not HA's `.storage`
files or credentials.

## Dataset fields and trust levels

The default capture is **cache-only** and generates **zero extra API calls**:

| Field | Provenance | Meaning |
| --- | --- | --- |
| `captured_at` | HA local UTC clock | JSON creation time, **not** when each sensor was read |
| `freshness.last_rest_success_at` | Coordinator | Most recent successful device list refresh, when instrumented |
| `freshness.last_ws_event_at` | Coordinator | Last processed device WebSocket event, when instrumented |
| `websocket_events` | Coordinator limited in-memory buffer | Last 100 events, timestamp/type/property key names only; no raw packets |
| `devices[].status` | Existing coordinator cache | API-derived values in HA; may be stale and may mix REST/push |
| `devices[].property_capabilities` | Existing device parser | Integration declarations, **not** proof of physical support |
| `devices[].api_declared_properties` | Cached `/clife-svc/get_property_list` reply | API-declared capabilities (when cached); not live values |
| `devices[].static_capabilities` | Cached `/clife-svc/pu/query_static_data` reply | API static flags when fetched |
| `devices[].status_units` | Integration mapping | Expected/assumed units; validate actual model and firmware |

Device aliases use a fresh random salt for each download. Strings that might
contain names, hardware IDs, URLs, network settings or credentials are redacted.
Never regard this as a guarantee for arbitrary future vendor schema changes:
inspect a diagnostic report before sharing it outside the trusted environment.

## Optional API reads

The library function `async_collect_snapshot(coordinator,
include_energy=True, include_self_check=True)` explicitly enables at most
one extra hourly-power request and one self-check request per device. Neither
is invoked by HA's standard Download diagnostics action. Both are read-only,
and errors are recorded by exception type only. They should be called only in
an HA developer/test harness that already has a live coordinator; do not
extract the OAuth token into another process or run an independent login.

## Follow-up comparisons

Capture snapshots while the user naturally operates the units in OFF, DRY,
COOL and HEAT. Never switch modes automatically for diagnostics. Compare
status value changes, `last_rest_success_at`, `last_ws_event_at`, and
`websocket_events` against the entity-state history, taking care that source
timestamps are coordinator-wide, not per property. This is necessary before
claiming a particular firmware refreshes room humidity while powered off.

For the API→entity baseline see `API_ENTITY_MAPPING.md`; REA/JADX findings and
limitations are in `docs/rea_connectlife_audit.md`.

## Real first download and accuracy lessons (2026-10-12)

The user supplied an authentic Home Assistant diagnostics JSON. It contained
three online type `009` / feature `128` units and a successful REST refresh
about 15 seconds before the download. All three were powered OFF. One still
had `t_work_mode=3`: this is a retained DRY selection and **not** evidence
of an actively running dehumidification cycle. Temperatures `f_temp_in`
were present (25, 27, 24) but humidity `f_humidity` and live power
`f_power_display` were absent. The `f_e_inhumidity=0` values refer to sensor
fault flags rather than relative-humidity measurements. `f_votage=1` must
not be shown as a verified 1 V supply-voltage reading. The first download
contained no captured push events, which does not establish WS failure.

For newer diagnostics, `status_units` includes only observed status keys;
ambiguous vendor scaling and locally estimated power must be called out
separately. The capture timestamp identifies export time, **never** the
measurement time of every property. Compare raw values with successive
downloads and HA recorder history before classifying one as stale.

## Read-only Home Assistant state/history cross-check — 2026-10-12 14:19 UTC

This comparison used only Home Assistant entity state and recorder history.
It did **not** query the ConnectLife API directly and therefore must not be
described as a new raw-API capture. HA timestamps below describe HA entity
updates/reports, not necessarily the instant a physical sensor was sampled.

At the 14:03:55 UTC baseline boundary (16:03:55 Europe/Rome), recorder state
for the three mapped 009/128 units matched the first diagnostics download:

| Unit | Climate | Indoor temperature | HA power-consumption entity |
| --- | --- | --- | --- |
| Camera | OFF | 24 °C | 0.03 kWh |
| Sala | OFF | 25 °C | 0.00 kWh |
| Studio | OFF | 27 °C | 0.00 kWh |

At the later read around 14:19 UTC all three climates were still OFF. Current
and locally estimated power were 0 on all three units. Camera temperature had
changed from 24 to 26 °C and both the climate entity and standalone indoor
temperature entity were reported by HA at 16:06:40 Europe/Rome. Sala and
Studio remained at 25 and 27 °C respectively; their climate `last_reported`
timestamps were still 16:02:41, before the native diagnostics capture. This is
an important freshness distinction: entity `last_reported` and coordinator
`last_rest_success_at` measure different layers and cannot be substituted for
one another. A lack of a newer HA entity report is not proof that the vendor
cloud or WebSocket did not refresh.

The current HA entity inventory still exposed no indoor-humidity measurement
and no reported-power entity corresponding to `f_power_display`. The existing
power entity is explicitly the integration's **estimated** power. All three
voltage entities still showed `1.0 V`; because the authentic diagnostic also
contained `f_votage=1` and the vendor scaling remains unknown, this must not be
treated as a physically validated one-volt mains reading.

### Recorder evidence of an actually running DRY cycle

The previous 24 hours contain stronger evidence than the OFF baseline for the
Camera unit. Home Assistant recorder shows its climate entity in `dry` from
23:01:36 on 2026-10-11 until 00:05:16 on 2026-10-12 (Europe/Rome). During that
same interval the HA current sensor changed from 0 to **1310 mA** at 23:03:56,
and estimated power changed from 0 to **281 W**, later **274 W**. This electrical
activity distinguishes that interval from the retained `t_work_mode=3` seen
while the device was OFF in the 14:03 UTC diagnostic snapshot.

The Camera HA power-consumption entity also advanced from 2.27 to 2.33 kWh at
23:29:16 and to 2.40 kWh at 23:45:46 while the climate remained in DRY. It then
reset to 0.00 kWh at 00:00:16 and later reported 0.03 kWh at 00:32:30. That HA
history is evidence that the exposed consumption value behaves like a
day-resetting total, rather than a lifetime monotonic meter. The 0.03 kWh update
after the climate had already switched OFF also shows why this entity should
not be interpreted as instantaneous power or as a precise on/off timestamp.

These observations establish active DRY operation and correlated electrical
activity at the HA entity layer only. They do not establish the raw vendor
payload, the physical voltage scale, or per-property ConnectLife freshness.

An offline, read-only comparison helper is available at
`tools/compare_diagnostics.py`:

```powershell
py -3 tools/compare_diagnostics.py old-diagnostic.json new-diagnostic.json
```

It compares counts, observed fields, online/powered-on counts, running modes,
and REST/WS evidence by device type/feature. **Device aliases are newly salted
for every export**, so the tool deliberately does not pretend to correlate
individual units across snapshots. Comparisons inside one 009/128 group are
aggregated, not proof of a particular unit's changing temperature. Run it on
trusted local JSON only; it does not contact HA or ConnectLife.
