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
