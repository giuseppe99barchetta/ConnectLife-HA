from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from custom_components.hisense_ac_plugin import async_unload_entry
from custom_components.hisense_ac_plugin.const import DOMAIN, StatusKey
from custom_components.hisense_ac_plugin.climate import (
    HisenseClimate,
    async_setup_entry as setup_climate,
)
from custom_components.hisense_ac_plugin.coordinator import (
    HisenseACPluginDataUpdateCoordinator,
)
from custom_components.hisense_ac_plugin.models import DeviceInfo
from custom_components.hisense_ac_plugin.switch import async_setup_entry as setup_switches
from custom_components.hisense_ac_plugin.switch import HisenseSwitch
from custom_components.hisense_ac_plugin.water_heater import (
    HisenseWaterHeater,
    STATE_DUAL_MODE,
)
from custom_components.hisense_ac_plugin.websocket import HisenseWebSocket


class DummyLoop:
    def call_soon_threadsafe(self, callback, *args):
        callback(*args)


class DummyHass:
    def __init__(self):
        self.loop = DummyLoop()
        self.helpers = SimpleNamespace(
            dispatcher=SimpleNamespace(async_dispatcher_send=lambda *args, **kwargs: None)
        )
        self.config = SimpleNamespace(language="en", time_zone="UTC")
        self.data = {
            f"{DOMAIN}.translations": {
                "en": {
                    "STATE_OFF": "Off",
                    "STATE_AUTO": "Auto",
                    "STATE_ELECTRIC": "Electric heating",
                    "STATE_DUAL_MODE": "Dual Mode",
                    "STATE_DUAL_MODE_": "Boost",
                    "STATE_DUAL_1": "Fast",
                    "STATE_DUAL_1_": "Boost 1",
                    "eco_mode": "Eco",
                }
            }
        }

    def async_create_task(self, coro):
        return asyncio.create_task(coro)


def build_device(
    *,
    device_id="dev1",
    puid="puid-1",
    type_code="016",
    feature_code="500",
    offline_state=1,
    status=None,
):
    return DeviceInfo(
        {
            "deviceId": device_id,
            "puid": puid,
            "deviceNickName": "Device",
            "deviceFeatureCode": feature_code,
            "deviceFeatureName": "Feature",
            "deviceTypeCode": type_code,
            "deviceTypeName": "Type",
            "offlineState": offline_state,
            "statusList": status or {StatusKey.POWER: "1"},
        }
    )


def test_websocket_wifi_status_marks_offline_state_zero_as_online():
    hass = DummyHass()
    coordinator = HisenseACPluginDataUpdateCoordinator(
        hass,
        api_client=SimpleNamespace(),
        config_entry=SimpleNamespace(),
    )
    coordinator._devices = {"dev1": build_device(offline_state=1)}

    coordinator._handle_ws_message(
        {
            "msgTypeCode": "status_wifistatus",
            "content": json.dumps({"puid": "puid-1", "onlinestats": 1}),
        }
    )

    assert coordinator._devices["dev1"].offline_state == 0
    assert coordinator._devices["dev1"].is_online is True


def test_water_heater_uses_normalized_mode_for_temperature_range():
    hass = DummyHass()
    device = build_device(
        status={
            StatusKey.POWER: "1",
            StatusKey.MODE: "10",
            StatusKey.WATER_TANK_TEMP: "42",
            StatusKey.TARGET_TEMP: "55",
        }
    )
    parser = SimpleNamespace(
        attributes={
            StatusKey.MODE: SimpleNamespace(
                value_map={"10": "双能热水模式"},
            )
        }
    )
    coordinator = SimpleNamespace(
        hass=hass,
        api_client=SimpleNamespace(parsers={device.device_id: parser}),
        get_device=lambda _device_id: device,
        async_control_device=None,
    )

    heater = HisenseWaterHeater(coordinator, device)

    assert heater.current_mode == STATE_DUAL_MODE
    assert heater._attr_min_temp == 15
    assert heater._attr_max_temp == 65
    assert heater.current_operation == "Boost"
    assert heater.current_mode == STATE_DUAL_MODE


def test_switch_setup_skips_missing_parser_without_crashing():
    hass = DummyHass()
    entry = SimpleNamespace(entry_id="entry-1")
    device = build_device(type_code="007", feature_code="299")
    coordinator = SimpleNamespace(
        hass=hass,
        data={device.device_id: device},
        api_client=SimpleNamespace(parsers={}, static_data={}),
    )
    hass.data[DOMAIN] = {entry.entry_id: coordinator}
    added = []

    async def run_test():
        await setup_switches(hass, entry, added.extend)

    asyncio.run(run_test())

    assert added == []


def test_async_unload_entry_calls_coordinator_cleanup():
    hass = DummyHass()
    cleanup_calls = []
    entry = SimpleNamespace(entry_id="entry-1")

    class ConfigEntriesManager:
        async def async_unload_platforms(self, config_entry, platforms):
            return True

    class Coordinator:
        async def async_unload(self):
            cleanup_calls.append("cleanup")

    hass.config_entries = ConfigEntriesManager()
    hass.data[DOMAIN] = {entry.entry_id: Coordinator()}

    result = asyncio.run(async_unload_entry(hass, entry))

    assert result is True
    assert cleanup_calls == ["cleanup"]
    assert entry.entry_id not in hass.data[DOMAIN]


def test_switch_scheduled_update_does_not_await_none():
    switch = HisenseSwitch.__new__(HisenseSwitch)
    switch._last_action_time = 0
    switch._debounce_delay = 10
    calls = []
    switch.async_schedule_update_ha_state = lambda force: calls.append(force)

    asyncio.run(HisenseSwitch._async_schedule_update(switch))

    assert calls == [True]


def test_climate_setup_accepts_split_ac_family_009_128():
    hass = DummyHass()
    entry = SimpleNamespace(entry_id="entry-1")
    device = build_device(
        type_code="009",
        feature_code="128",
        status={
            StatusKey.POWER: "1",
            StatusKey.TEMPERATURE: "24",
            StatusKey.TARGET_TEMP: "25",
            StatusKey.MODE: "0",
            StatusKey.T_TEMP_TYPE: "0",
        },
    )
    coordinator = SimpleNamespace(
        hass=hass,
        data={device.device_id: device},
        api_client=SimpleNamespace(parsers={}, static_data={}),
        async_config_entry_first_refresh=_async_noop,
    )
    hass.data[DOMAIN] = {entry.entry_id: coordinator}
    added = []

    async def run_test():
        await setup_climate(hass, entry, added.extend)

    asyncio.run(run_test())

    assert device.is_air_conditioner() is True
    assert len(added) == 1
    assert isinstance(added[0], HisenseClimate)


def test_websocket_async_connect_is_non_blocking_and_cancellable():
    events = []

    class TestWebSocket(HisenseWebSocket):
        async def _run_forever(self):
            events.append("started")
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                events.append("cancelled")
                raise

    async def run_test():
        hass = DummyHass()
        ws = TestWebSocket(
            hass,
            api_client=SimpleNamespace(),
            message_callback=lambda message: None,
        )
        await ws.async_connect()
        await asyncio.sleep(0)
        assert ws._task is not None
        assert "started" in events
        await ws.async_disconnect()
        assert ws._task is None
        assert "cancelled" in events

    asyncio.run(run_test())


def test_websocket_connect_failure_does_not_block_startup():
    class TestWebSocket(HisenseWebSocket):
        async def _run_forever(self):
            raise RuntimeError("boom")

    async def run_test():
        hass = DummyHass()
        ws = TestWebSocket(
            hass,
            api_client=SimpleNamespace(),
            message_callback=lambda message: None,
        )
        await ws.async_connect()
        await asyncio.sleep(0)
        assert ws._task is not None
        await ws.async_disconnect()

    asyncio.run(run_test())


async def _async_noop():
    return None
