from __future__ import annotations

import asyncio
import copy
import json
from datetime import date
from types import SimpleNamespace

from custom_components.hisense_ac_plugin.diagnostics import (
    async_collect_snapshot,
    sanitize_snapshot,
)
from custom_components.hisense_ac_plugin.models import DeviceInfo


def _build_device() -> DeviceInfo:
    device = DeviceInfo(
        {
            "deviceId": "device-secret-id",
            "puid": "puid-secret-id",
            "wifiId": "wifi-secret-id",
            "deviceNickName": "Bedroom AC",
            "deviceFeatureCode": "128",
            "deviceFeatureName": "Split AC",
            "deviceTypeCode": "009",
            "deviceTypeName": "Air conditioner",
            "roomId": "room-secret-id",
            "roomName": "Bedroom",
            "offlineState": 1,
            "statusList": {
                "t_power": "1",
                "f_temp_in": "22.5",
                "f_humidity": "61",
            },
        }
    )
    device.failed_data = ["f_e_intemp"]
    return device


class FakeApi:
    def __init__(self, device: DeviceInfo) -> None:
        self.parsers = {
            device.device_id: SimpleNamespace(
                attributes={
                    "f_temp_in": SimpleNamespace(
                        key="f_temp_in",
                        name="Indoor Temperature",
                        attr_type="Number",
                        step=1,
                        value_range=None,
                        value_map=None,
                        read_write="R",
                    )
                }
            )
        }
        self.static_data = {
            device.device_id: {
                "f_humidity": "1",
                "Mode_settings": "1",
            }
        }
        self.energy_calls: list[tuple[tuple, dict]] = []
        self.self_check_calls: list[tuple[tuple, dict]] = []

    async def async_get_devices(self):
        raise AssertionError("diagnostic collection must use coordinator cache")

    async def async_control_device(self, *args, **kwargs):
        raise AssertionError("diagnostic collection must never control a device")

    async def async_get_hour_power(self, *args, **kwargs):
        self.energy_calls.append((args, kwargs))
        return {
            "success": True,
            "status": {
                "08": "0.25",
                "09": "0.50",
            },
        }

    async def async_api_self_check(self, *args, **kwargs):
        self.self_check_calls.append((args, kwargs))
        return {
            "success": True,
            "status": {
                "result": "self-check-sentinel",
                "code": 0,
            },
        }


def _build_coordinator() -> tuple[SimpleNamespace, FakeApi, DeviceInfo]:
    device = _build_device()
    api = FakeApi(device)
    coordinator = SimpleNamespace(
        data={device.device_id: device},
        api_client=api,
    )
    return coordinator, api, device


def _snapshot_text(snapshot) -> str:
    return json.dumps(snapshot, sort_keys=True, default=str)


def _call_includes_value(call: tuple[tuple, dict], value: str) -> bool:
    args, kwargs = call
    return value in args or value in kwargs.values()


def test_collect_snapshot_uses_cached_read_only_data_by_default():
    coordinator, api, device = _build_coordinator()

    snapshot = asyncio.run(async_collect_snapshot(coordinator))
    item = snapshot["devices"][0]

    assert snapshot["schema_version"] == 1
    assert snapshot["source"] == "home_assistant_connectlife_coordinator_cache"
    assert len(snapshot["devices"]) == 1
    assert item["type_code"] == "009"
    assert item["feature_code"] == "128"
    assert item["online"] is True
    assert item["status"]["f_temp_in"] == "22.5"
    assert item["status"]["f_humidity"] == "61"
    assert item["status_units"]["f_temp_in"] == "°C"
    assert item["status_units"]["f_humidity"] == "%"
    assert item["property_capabilities"]["f_temp_in"]["read_write"] == "R"
    assert item["static_capabilities"]["f_humidity"] == "1"
    assert device.device_id not in _snapshot_text(snapshot)
    assert device.puid not in _snapshot_text(snapshot)
    assert api.energy_calls == []
    assert api.self_check_calls == []


def test_collect_snapshot_calls_optional_read_only_endpoints_when_requested():
    coordinator, api, device = _build_coordinator()

    snapshot = asyncio.run(
        async_collect_snapshot(
            coordinator,
            include_energy=True,
            include_self_check=True,
        )
    )
    serialized = _snapshot_text(snapshot)

    assert len(api.energy_calls) == 1
    assert len(api.self_check_calls) == 1
    assert api.energy_calls[0] == ((date.today().isoformat(), device.puid), {})
    assert api.self_check_calls[0] == (("0", device.puid), {})
    item = snapshot["devices"][0]
    assert item["energy_read"]["success"] is True
    assert item["energy_read"]["status"] == {"08": "0.25", "09": "0.50"}
    assert item["energy_source"] == "live_connectlife_hourly_api"
    assert item["self_check_read"]["success"] is True
    assert item["self_check_read"]["status"]["code"] == 0
    assert item["self_check_read"]["status"]["result"] == "<redacted>"
    assert item["self_check_source"] == "live_connectlife_self_check_api"
    assert "self-check-sentinel" not in serialized


def test_sanitize_snapshot_redacts_identifiers_and_credentials_recursively():
    snapshot = {
        "accessToken": "access-token-secret",
        "refresh_token": "refresh-token-secret",
        "authorization": "Bearer authorization-secret",
        "device": {
            "deviceId": "device-secret-id",
            "puid": "puid-secret-id",
            "wifiId": "wifi-secret-id",
            "deviceNickName": "Bedroom AC",
            "roomId": "room-secret-id",
            "roomName": "Bedroom",
            "statusList": {
                "f_temp_in": "22.5",
                "f_humidity": "61",
            },
        },
        "capabilities": {
            "deviceTypeCode": "009",
            "deviceFeatureCode": "128",
            "propertyList": [
                {
                    "propertyKey": "f_temp_in",
                    "readWrite": "R",
                }
            ],
        },
    }
    original = copy.deepcopy(snapshot)

    sanitized = sanitize_snapshot(snapshot)
    serialized = _snapshot_text(sanitized)

    for secret in (
        "access-token-secret",
        "refresh-token-secret",
        "authorization-secret",
        "device-secret-id",
        "puid-secret-id",
        "wifi-secret-id",
        "Bedroom AC",
        "room-secret-id",
        "Bedroom",
    ):
        assert secret not in serialized

    assert "f_temp_in" in serialized
    assert "22.5" in serialized
    assert "f_humidity" in serialized
    assert "61" in serialized
    assert "009" in serialized
    assert "128" in serialized
    assert snapshot == original
