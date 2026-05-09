from __future__ import annotations

import asyncio
import json
import re
from types import SimpleNamespace

from custom_components.hisense_ac_plugin import async_unload_entry
from custom_components.hisense_ac_plugin.const import DOMAIN, StatusKey
from custom_components.hisense_ac_plugin.climate import (
    HisenseClimate,
    async_setup_entry as setup_climate,
)
from custom_components.hisense_ac_plugin import climate as climate_module
from homeassistant.components.climate import HVACMode
from homeassistant.components.climate.const import SWING_OFF, SWING_VERTICAL
from custom_components.hisense_ac_plugin.config_flow import HisenseOptionsFlowHandler
from custom_components.hisense_ac_plugin.coordinator import (
    HisenseACPluginDataUpdateCoordinator,
)
from custom_components.hisense_ac_plugin import humidifier as humidifier_module
from custom_components.hisense_ac_plugin.models import DeviceInfo
from custom_components.hisense_ac_plugin import number as number_module
from custom_components.hisense_ac_plugin import sensor as sensor_module
from custom_components.hisense_ac_plugin.number import async_setup_entry as setup_number
from custom_components.hisense_ac_plugin.humidifier import async_setup_entry as setup_humidifier
from custom_components.hisense_ac_plugin.switch import async_setup_entry as setup_switches
from custom_components.hisense_ac_plugin.switch import HisenseSwitch
from custom_components.hisense_ac_plugin import water_heater as water_heater_module
from custom_components.hisense_ac_plugin.water_heater import (
    HisenseWaterHeater,
    STATE_DUAL_MODE,
    async_setup_entry as setup_water_heater,
)
from custom_components.hisense_ac_plugin.websocket import HisenseWebSocket

MISSING = object()

class DummyLoop:
    def call_soon_threadsafe(self, callback, *args):
        callback(*args)

    def call_soon(self, callback, *args):
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

def build_device(
    *,
    device_id="dev1",
    puid="puid-1",
    type_code="016",
    feature_code="500",
    offline_state=1,
    status=None,
):
    data = {
        "deviceId": device_id,
        "puid": puid,
        "deviceNickName": "Device",
        "deviceFeatureCode": feature_code,
        "deviceFeatureName": "Feature",
        "deviceTypeCode": type_code,
        "deviceTypeName": "Type",
        "statusList": status or {StatusKey.POWER: "1"},
    }
    if offline_state is not MISSING:
        data["offlineState"] = offline_state
    return DeviceInfo(data)


def test_websocket_wifi_status_marks_offline_state_one_as_online():
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

    assert coordinator._devices["dev1"].offline_state == 1
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


def test_rapid_mode_switch_for_009_128_is_created_and_available():
    hass = DummyHass()
    entry = SimpleNamespace(entry_id="entry-1")
    device = build_device(
        type_code="009",
        feature_code="128",
        status={
            StatusKey.POWER: "1",
            StatusKey.MODE: "0",
            StatusKey.RAPID: "0",
            StatusKey.TARGET_TEMP: "25",
            StatusKey.FAN_SPEED: "9",
        },
    )
    parser = SimpleNamespace(
        attributes={
            StatusKey.RAPID: SimpleNamespace(),
            StatusKey.MODE: SimpleNamespace(),
        }
    )
    coordinator = SimpleNamespace(
        hass=hass,
        data={device.device_id: device},
        api_client=SimpleNamespace(parsers={device.device_id: parser}, static_data={}),
        get_device=lambda _device_id: device,
    )
    hass.data[DOMAIN] = {entry.entry_id: coordinator}
    added = []

    async def run_test():
        await setup_switches(hass, entry, added.extend)

    asyncio.run(run_test())

    rapid_entities = [entity for entity in added if getattr(entity, "_switch_type", None) == "rapid_mode"]
    assert len(rapid_entities) == 1
    assert rapid_entities[0].available is True


def test_rapid_mode_switch_for_009_128_is_skipped_without_t_super():
    hass = DummyHass()
    entry = SimpleNamespace(entry_id="entry-1")
    device = build_device(
        type_code="009",
        feature_code="128",
        status={
            StatusKey.POWER: "1",
            StatusKey.MODE: "0",
            StatusKey.TARGET_TEMP: "25",
            StatusKey.FAN_SPEED: "9",
        },
    )
    parser = SimpleNamespace(
        attributes={
            StatusKey.MODE: SimpleNamespace(),
        }
    )
    coordinator = SimpleNamespace(
        hass=hass,
        data={device.device_id: device},
        api_client=SimpleNamespace(parsers={device.device_id: parser}, static_data={}),
        get_device=lambda _device_id: device,
    )
    hass.data[DOMAIN] = {entry.entry_id: coordinator}
    added = []

    async def run_test():
        await setup_switches(hass, entry, added.extend)

    asyncio.run(run_test())

    rapid_entities = [entity for entity in added if getattr(entity, "_switch_type", None) == "rapid_mode"]
    assert rapid_entities == []


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


def test_options_flow_uses_private_config_entry_storage():
    entry = SimpleNamespace(entry_id="entry-1", data={})
    flow = HisenseOptionsFlowHandler(entry)
    assert flow._config_entry is entry


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


def test_climate_009_128_available_when_offline_state_one_int():
    climate = _build_climate_device_for_availability(1)
    assert climate.available is True


def test_climate_009_128_available_when_offline_state_one_str():
    climate = _build_climate_device_for_availability("1")
    assert climate.available is True


def test_climate_009_128_available_when_offline_state_missing_but_status_present():
    climate = _build_climate_device_for_availability(MISSING)
    assert climate.available is True


def test_sensor_metadata_uses_english_fallback_strings():
    for sensor_type, sensor_info in sensor_module.SENSOR_TYPES.items():
        assert not _contains_chinese(sensor_info["name"]), sensor_type
        assert not _contains_chinese(sensor_info["description"]), sensor_type


def test_switch_metadata_uses_english_fallback_strings_for_dehumidifier_fan_speed():
    hass = DummyHass()
    entry = SimpleNamespace(entry_id="entry-1")
    device = build_device(
        type_code="007",
        feature_code="299",
        status={
            StatusKey.POWER: "1",
            StatusKey.FAN_SPEED: "2",
        },
    )
    parser = SimpleNamespace(
        attributes={
            StatusKey.FAN_SPEED: SimpleNamespace(
                key=StatusKey.FAN_SPEED,
                value_map={"0": "低风", "1": "高风", "2": "自动", "3": "中风"},
            )
        }
    )
    coordinator = SimpleNamespace(
        hass=hass,
        data={device.device_id: device},
        api_client=SimpleNamespace(
            parsers={device.device_id: parser},
            static_data={
                device.device_id: {
                    "Wind_speed_gear_selection_auto": "1",
                    "Wind_speed_gear_selection_middle": "1",
                    "Wind_speed_gear_selection_high": "1",
                    "Wind_speed_gear_selection_low": "1",
                }
            },
        ),
    )
    hass.data[DOMAIN] = {entry.entry_id: coordinator}
    added = []

    async def run_test():
        await setup_switches(hass, entry, added.extend)

    asyncio.run(run_test())

    fan_speed_entities = [
        entity for entity in added if getattr(entity, "_switch_type", "").startswith("fan_speed_")
    ]

    assert fan_speed_entities
    for entity in fan_speed_entities:
        assert not _contains_chinese(entity._switch_info["name"]), entity._switch_type
        assert not _contains_chinese(entity._switch_info["description"]), entity._switch_type


def test_climate_009_128_target_temp_uses_t_temp_key():
    climate = _build_climate_with_parser(
        status={
            StatusKey.POWER: "1",
            StatusKey.TEMPERATURE: "24",
            StatusKey.TARGET_TEMP: "25",
            StatusKey.MODE: "2",
            StatusKey.T_TEMP_TYPE: "0",
        }
    )
    assert climate.target_temperature == 25.0


def test_climate_009_128_missing_temp_range_logs_debug_not_warning():
    warnings = []
    debug_messages = []
    original_warning = climate_module._LOGGER.warning
    original_debug = climate_module._LOGGER.debug
    climate_module._LOGGER.warning = lambda message, *args, **kwargs: warnings.append(
        message % args if args else message
    )
    climate_module._LOGGER.debug = lambda message, *args, **kwargs: debug_messages.append(
        message % args if args else message
    )
    try:
        _build_climate_with_parser(
            parser_attrs={
                StatusKey.MODE: SimpleNamespace(value_map={"0": "送风", "1": "制热", "2": "制冷", "3": "除湿", "4": "自动"}),
                StatusKey.FAN_SPEED: SimpleNamespace(value_map={"0": "自动", "6": "中低", "7": "中", "8": "中高", "9": "高"}),
                StatusKey.SWING: SimpleNamespace(value_map={"0": "取消", "1": "开启"}),
            }
        )
    finally:
        climate_module._LOGGER.warning = original_warning
        climate_module._LOGGER.debug = original_debug

    assert all("Target temperature attribute or value range not found" not in message for message in warnings)
    assert any("Target temperature attribute or value range not found" in message for message in debug_messages)


def test_climate_009_128_mode_zero_maps_to_fan_only():
    climate = _build_climate_with_parser(
        status={
            StatusKey.POWER: "1",
            StatusKey.TEMPERATURE: "24",
            StatusKey.TARGET_TEMP: "25",
            StatusKey.MODE: "0",
            StatusKey.T_TEMP_TYPE: "0",
        }
    )
    assert climate.hvac_mode == HVACMode.FAN_ONLY


def test_climate_009_128_fan_speed_fallback_and_english_modes():
    climate = _build_climate_with_parser(
        status={
            StatusKey.POWER: "1",
            StatusKey.TEMPERATURE: "24",
            StatusKey.TARGET_TEMP: "25",
            StatusKey.MODE: "2",
            StatusKey.T_TEMP_TYPE: "0",
            "t_fan_speed_s": "9",
        }
    )
    assert climate.fan_mode == "high"
    assert "ultra_low" in climate.fan_modes
    assert "ultra_high" in climate.fan_modes


def test_climate_009_128_vertical_swing_only():
    climate = _build_climate_with_parser(
        status={
            StatusKey.POWER: "1",
            StatusKey.TEMPERATURE: "24",
            StatusKey.TARGET_TEMP: "25",
            StatusKey.MODE: "2",
            StatusKey.T_TEMP_TYPE: "0",
            StatusKey.SWING: "1",
        },
        parser_attrs={
            StatusKey.MODE: SimpleNamespace(value_map={"0": "送风", "1": "制热", "2": "制冷", "3": "除湿", "4": "自动"}),
            StatusKey.TARGET_TEMP: SimpleNamespace(value_range="16~32,61~90"),
            StatusKey.FAN_SPEED: SimpleNamespace(value_map={"0": "自动", "6": "中低", "7": "中", "8": "中高", "9": "高"}),
            StatusKey.SWING: SimpleNamespace(value_map={"0": "取消", "1": "开启"}),
        },
        static_data={
            "Upper_and_lower_damper_control": "1",
            "Left_and_right_damper_control": "0",
        },
    )
    assert climate.swing_mode == SWING_VERTICAL
    assert climate._attr_swing_modes == [SWING_OFF, SWING_VERTICAL]


def test_no_warning_for_ac_only_empty_number_platform():
    _assert_no_no_entities_warning(number_module, setup_number)


def test_no_warning_for_ac_only_empty_water_heater_platform():
    _assert_no_no_entities_warning(water_heater_module, setup_water_heater)


def test_no_warning_for_ac_only_empty_humidifier_platform():
    _assert_no_no_entities_warning(humidifier_module, setup_humidifier)


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
        ws.async_connect()
        await asyncio.sleep(0)
        assert ws._ws_task is not None
        assert "started" in events
        await ws.async_disconnect()
        assert ws._ws_task is None
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
        ws.async_connect()
        await asyncio.sleep(0)
        assert ws._ws_task is not None
        await ws.async_disconnect()

    asyncio.run(run_test())


async def _async_noop():
    return None


def _build_climate_device_for_availability(offline_state):
    hass = DummyHass()
    device = build_device(
        type_code="009",
        feature_code="128",
        offline_state=offline_state,
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
        api_client=SimpleNamespace(parsers={device.device_id: None}, static_data={}),
        get_device=lambda _device_id: device,
    )
    return HisenseClimate(coordinator, device)


def _build_climate_with_parser(status=None, parser_attrs=None, static_data=None):
    hass = DummyHass()
    device = build_device(
        type_code="009",
        feature_code="128",
        status=status
        or {
            StatusKey.POWER: "1",
            StatusKey.TEMPERATURE: "24",
            StatusKey.TARGET_TEMP: "25",
            StatusKey.MODE: "2",
            StatusKey.T_TEMP_TYPE: "0",
            StatusKey.FAN_SPEED: "9",
            StatusKey.SWING: "0",
        },
    )
    default_parser_attrs = {
        StatusKey.MODE: SimpleNamespace(value_map={"0": "送风", "1": "制热", "2": "制冷", "3": "除湿", "4": "自动"}),
        StatusKey.TARGET_TEMP: SimpleNamespace(value_range="16~32,61~90"),
        StatusKey.FAN_SPEED: SimpleNamespace(value_map={"0": "自动", "6": "中低", "7": "中", "8": "中高", "9": "高"}),
        StatusKey.SWING: SimpleNamespace(value_map={"0": "取消", "1": "开启"}),
    }
    if parser_attrs is not None:
        default_parser_attrs = parser_attrs
    parser = SimpleNamespace(attributes=default_parser_attrs)
    coordinator = SimpleNamespace(
        hass=hass,
        api_client=SimpleNamespace(
            parsers={device.device_id: parser},
            static_data={device.device_id: static_data or {}},
        ),
        get_device=lambda _device_id: device,
        async_control_device=None,
    )
    return HisenseClimate(coordinator, device)


def _assert_no_no_entities_warning(module, setup_fn):
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
    warnings = []
    original_warning = module._LOGGER.warning
    module._LOGGER.warning = lambda message, *args, **kwargs: warnings.append(
        message % args if args else message
    )
    try:
        asyncio.run(setup_fn(hass, entry, added.extend))
    finally:
        module._LOGGER.warning = original_warning

    assert added == []
    assert all("No supported" not in message for message in warnings)


def _contains_chinese(value: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", value))
