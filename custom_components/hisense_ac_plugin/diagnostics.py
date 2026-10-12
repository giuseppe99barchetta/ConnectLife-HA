"""Read-only, privacy-preserving ConnectLife diagnostics for Home Assistant.

The standard HA diagnostics download reuses the *live* coordinator and its
OAuth session; it never requests, reads, prints or exports the OAuth token.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import re
import secrets
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_SENSITIVE = ("token", "secret", "password", "authorization", "cookie", "session",
              "email", "phone", "address", "ssid", "wifi", "puid", "deviceid",
              "userid", "openid", "longitude", "latitude", "serial", "imei",
              "mac", "credential", "roomid", "homeid", "houseid", "familyid",
              "accountid", "memberid", "uuid", "nickname", "roomname",
              "devicename")

_SAFE_MAPPING_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_SAFE_NUMERIC_KEY = re.compile(r"^\d{1,4}$")
_SAFE_DATE_KEY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_LIKELY_IDENTIFIER_KEY = re.compile(r"^(?:[0-9A-Fa-f]{16,}|[A-Za-z0-9]{24,})$")
_SAFE_PROPERTY_VALUE = re.compile(r"^(?:[ft]_[a-z0-9_]+|status_[a-z0-9_]+)$")
_SAFE_RANGE_VALUE = re.compile(r"^-?\d+(?:\.\d+)?(?:~-?\d+(?:\.\d+)?)?(?:,-?\d+(?:\.\d+)?(?:~-?\d+(?:\.\d+)?)?)*$")
_SAFE_ISO_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)

_STATUS_UNITS = {
    "f_temp_in": "°C",
    "t_temp_in": "°C",
    "f_humidity": "%",
    "f_power_display": "W",
    "f_electricity": "mA",
    "f_power_consumption": "kWh",
    "f_estimated_power": "W",
}


def _sanitize_mapping_key(label: str, salt: bytes) -> str:
    """Keep schema-like keys while anonymizing identifier-like map keys."""
    if (
        (_SAFE_MAPPING_KEY.fullmatch(label) and not _LIKELY_IDENTIFIER_KEY.fullmatch(label))
        or _SAFE_NUMERIC_KEY.fullmatch(label)
        or _SAFE_DATE_KEY.fullmatch(label)
    ):
        return label
    digest = hashlib.sha256(salt + label.encode()).hexdigest()[:12]
    return f"<redacted-key:{digest}>"


def sanitize_snapshot(value: Any, *, salt: bytes | None = None) -> Any:
    """Remove identifiers/credentials from arbitrary API payloads.

    Numeric and boolean values are retained. Strings are retained only for
    known non-identifying enumerations; arbitrary API strings are redacted.
    """
    salt = salt or secrets.token_bytes(16)
    if isinstance(value, dict):
        clean = {}
        for key, item in value.items():
            label = str(key)
            output_label = _sanitize_mapping_key(label, salt)
            is_device_property = bool(_SAFE_PROPERTY_VALUE.fullmatch(label))
            if not is_device_property and any(
                part in label.lower().replace("_", "") for part in _SENSITIVE
            ):
                clean[output_label] = "<redacted>"
            else:
                clean[output_label] = sanitize_snapshot(item, salt=salt)
        return clean
    if isinstance(value, (list, tuple)):
        return [sanitize_snapshot(item, salt=salt) for item in value]
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.lower() in {"on", "off", "online", "offline", "true", "false",
                                "auto", "dry", "cool", "heat", "fan", "unknown"}:
            return stripped
        if stripped in {"R", "RW", "W"}:
            return stripped
        if (
            _SAFE_PROPERTY_VALUE.fullmatch(stripped)
            or _SAFE_RANGE_VALUE.fullmatch(stripped)
            or _SAFE_ISO_TIMESTAMP.fullmatch(stripped)
        ):
            return stripped
        # API sensors often return numbers as strings.
        try:
            number = float(stripped)
            if number == number and abs(number) < 1e12:
                return stripped
        except ValueError:
            pass
        return "<redacted>"
    return "<redacted>"


async def async_collect_snapshot(
    coordinator: Any,
    include_energy: bool = False,
    include_self_check: bool = False,
) -> dict[str, Any]:
    """Build an anonymized live snapshot, with opt-in supplementary read calls.

    No refresh of the device list and no control commands. The default uses
    values already held by the coordinator, avoiding extra cloud polling.
    """
    capture_started_at = datetime.now(timezone.utc).isoformat()
    api = coordinator.api_client
    devices = coordinator.data or {}
    salt = secrets.token_bytes(16)
    result: dict[str, Any] = {
        "schema_version": 1,
        "capture_started_at": capture_started_at,
        "captured_at": capture_started_at,
        "source": "home_assistant_connectlife_coordinator_cache",
        "oauth": "existing_session_not_exported",
        "freshness": {
            "last_rest_success_at": getattr(coordinator, "last_rest_success_at", None),
            "last_ws_event_at": getattr(coordinator, "last_ws_event_at", None),
            "note": "Per-key timestamps are not provided by ConnectLife; cached values may be older than capture time.",
        },
        "devices": [],
        "websocket_events": sanitize_snapshot(
            list(getattr(coordinator, "_diagnostic_ws_events", [])), salt=salt
        ),
    }
    for device in devices.values():
        identity = str(device.device_id or device.puid or "")
        alias = hashlib.sha256(salt + identity.encode()).hexdigest()[:12]
        parser = api.parsers.get(device.device_id)
        attributes = getattr(parser, "attributes", {}) or {}
        property_list = getattr(api, "_property_lists", {}).get(
            (device.type_code, device.feature_code), []
        )
        status = getattr(device, "status", {}) or {}
        static = api.static_data.get(device.device_id) or {}
        item: dict[str, Any] = {
            "device_alias": alias,
            "type_code": sanitize_snapshot(device.type_code, salt=salt),
            "feature_code": sanitize_snapshot(device.feature_code, salt=salt),
            "online": device.is_online,
            "status": sanitize_snapshot(status, salt=salt),
            "status_units": {
                key: unit for key, unit in _STATUS_UNITS.items() if key in status
            },
            "status_value_notes": {
                key: note
                for key, note in {
                    "f_votage": "vendor_voltage_scale_unvalidated",
                    "f_power_consumption": "period_and_reset_semantics_unvalidated",
                    "f_estimated_power": "locally_calculated_not_hardware_measured",
                }.items()
                if key in status
            },
            "property_capabilities": {
                key: {"read_write": getattr(value, "read_write", None),
                      "range": sanitize_snapshot(getattr(value, "value_range", None), salt=salt),
                      "observed_in_status": key in status}
                for key, value in attributes.items()
            },
            "api_declared_properties": sanitize_snapshot(property_list, salt=salt),
            "static_capabilities": sanitize_snapshot(static, salt=salt),
            "status_source": "coordinator_cache_mixed_rest_and_websocket",
        }
        if include_energy and device.puid:
            # One optional read per device. Not used by HA's default diagnostics.
            try:
                from datetime import date
                energy = await api.async_get_hour_power(date.today().isoformat(), device.puid)
                item["energy_read"] = sanitize_snapshot(energy, salt=salt)
                item["energy_source"] = "live_connectlife_hourly_api"
                item["energy_read_at"] = datetime.now(timezone.utc).isoformat()
            except Exception as err:
                item["energy_error_type"] = type(err).__name__
                item["energy_attempted_at"] = datetime.now(timezone.utc).isoformat()
        if include_self_check and device.puid:
            try:
                check = await api.async_api_self_check("0", device.puid)
                item["self_check_read"] = sanitize_snapshot(check, salt=salt)
                item["self_check_source"] = "live_connectlife_self_check_api"
                item["self_check_read_at"] = datetime.now(timezone.utc).isoformat()
            except Exception as err:
                item["self_check_error_type"] = type(err).__name__
                item["self_check_attempted_at"] = datetime.now(timezone.utc).isoformat()
        result["devices"].append(item)
    result["captured_at"] = datetime.now(timezone.utc).isoformat()
    return result


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Native Home Assistant diagnostics download entry point."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    return await async_collect_snapshot(coordinator)
