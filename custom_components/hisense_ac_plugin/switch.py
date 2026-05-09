"""Platform for Hisense AC switch integration."""
from __future__ import annotations

import datetime
import logging
import re
import time
from typing import Any, Callable

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.core import Event
from homeassistant.helpers.dispatcher import callback

from .const import DOMAIN, StatusKey
from .coordinator import HisenseACPluginDataUpdateCoordinator
from .api import HisenseApiClient
from .models import DeviceInfo as HisenseDeviceInfo

_LOGGER = logging.getLogger(__name__)

# Define switch types
SWITCH_TYPES = {
    "quiet_mode": {
        "key": StatusKey.QUIET,
        "name": "Quiet Mode",
        "icon_on": "mdi:volume-off",
        "icon_off": "mdi:volume-high",
        "description": "Toggle quiet mode"
    },
    "rapid_mode": {
        "key": StatusKey.RAPID,
        "name": "Rapid Mode",
        "icon_on": "mdi:speedometer",
        "icon_off": "mdi:speedometer-slow",
        "description": "Toggle rapid (powerful) mode"
    },
    "8heat_mode": {
        "key": StatusKey.EIGHTHEAT,
        "name": "8heat Mode",
        "icon_on": "mdi:fire",
        "icon_off": "mdi:fire-off",
        "description": "Toggle 8heat mode"
    }
    # ,
    # "eco_mode": {
    #     "key": StatusKey.ECO,
    #     "name": "Eco Mode",
    #     "icon_on": "mdi:leaf",
    #     "icon_off": "mdi:leaf-off",
    #     "description": "Toggle eco mode"
    # }
}


def _has_switch_support(
    device: HisenseDeviceInfo,
    parser,
    switch_type: str,
    switch_info: dict[str, str],
    static_data: dict[str, Any] | None,
) -> bool:
    """Return whether a switch should be created for this device."""
    key = switch_info["key"]
    key_in_status = key in (device.status or {})
    key_in_parser = bool(parser and getattr(parser, "attributes", None) and key in parser.attributes)

    if switch_type == "rapid_mode":
        return key_in_status or key_in_parser

    if not (key_in_status or key_in_parser):
        return False

    if static_data:
        if switch_type == "quiet_mode":
            return static_data.get("Mute_mode_function") == "1"
        if switch_type == "8heat_mode":
            return True
    else:
        return key_in_status

    return True


def _build_zone_switch_definitions(device: HisenseDeviceInfo, parser) -> list[tuple[str, dict[str, str]]]:
    """Build dynamic zone switch definitions from parser attributes/status keys."""
    zone_definitions: list[tuple[str, dict[str, str]]] = []
    if not parser:
        return zone_definitions

    attrs = getattr(parser, "attributes", {}) or {}
    seen_keys: set[str] = set()

    def is_zone_switch_key(key_lower: str) -> bool:
        if "zone" not in key_lower:
            return False
        # Accept common zone switch naming styles from different regions/platforms.
        return (
            key_lower.endswith("_power")
            or key_lower.endswith("_switch")
            or "onoff" in key_lower
            or "enable" in key_lower
        )

    for key, attr in attrs.items():
        key_lower = key.lower()
        if not is_zone_switch_key(key_lower):
            continue

        value_range = getattr(attr, "value_range", "") or ""
        value_map = getattr(attr, "value_map", None)
        read_write = getattr(attr, "read_write", "RW")

        if read_write == "R":
            continue

        is_binary_range = value_range in {"0,1", "1,0"}
        is_binary_map = bool(value_map) and set(value_map.keys()) == {"0", "1"}
        if not (is_binary_range or is_binary_map):
            continue

        zone_match = re.search(r"zone_?(\d+)", key_lower)
        if zone_match:
            zone_name = f"Zone {zone_match.group(1)}"
            switch_type = f"zone_{zone_match.group(1)}"
        else:
            zone_name = key.replace("t_", "").replace("_", " ").title()
            switch_type = key_lower

        switch_info = {
            "key": key,
            "name": zone_name,
            "icon_on": "mdi:home-floor-1",
            "icon_off": "mdi:home-floor-0",
            "description": f"Toggle {zone_name}",
        }
        zone_definitions.append((switch_type, switch_info))
        seen_keys.add(key)

    # Fallback: discover binary zone toggles from live status keys.
    for key, value in (device.status or {}).items():
        if key in seen_keys:
            continue

        key_lower = str(key).lower()
        if not is_zone_switch_key(key_lower):
            continue

        normalized = str(value)
        if normalized not in {"0", "1"}:
            continue

        zone_match = re.search(r"zone_?(\d+)", key_lower)
        if zone_match:
            zone_name = f"Zone {zone_match.group(1)}"
            switch_type = f"zone_{zone_match.group(1)}"
        else:
            zone_name = key.replace("t_", "").replace("_", " ").title()
            switch_type = key_lower

        switch_info = {
            "key": key,
            "name": zone_name,
            "icon_on": "mdi:home-floor-1",
            "icon_off": "mdi:home-floor-0",
            "description": f"Toggle {zone_name}",
        }
        zone_definitions.append((switch_type, switch_info))
        seen_keys.add(key)

    if zone_definitions:
        _LOGGER.info("Detected %d zone switch attributes for device %s: %s", len(zone_definitions), device.name, [z[1]["key"] for z in zone_definitions])

    return zone_definitions

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Hisense AC switch platform."""
    coordinator: HisenseACPluginDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    try:
        # Get devices from coordinator
        devices = coordinator.data
        _LOGGER.debug("Setting up switches with coordinator data: %s", devices)

        if not devices:
            _LOGGER.warning("No devices found in coordinator data")
            return

        entities = []
        for device_id, device in devices.items():
            _LOGGER.debug("Processing device for switches: %s", device.to_dict())

            if isinstance(device, HisenseDeviceInfo) and device.is_devices():
                parser = coordinator.api_client.parsers.get(device.device_id)

                # Add switches for each supported feature
                for switch_type, switch_info in SWITCH_TYPES.items():
                    static_data = coordinator.api_client.static_data.get(device.device_id)
                    if _has_switch_support(device, parser, switch_type, switch_info, static_data):
                        _LOGGER.info(
                            "Adding %s switch for device: %s",
                            switch_info["name"],
                            device.name
                        )
                        _LOGGER.debug("Switch candidate feature=%s status=%s", device.feature_code, device.status)
                        # Align with ConnectLife: hide quiet mode for feature 200.
                        if switch_type == "quiet_mode":
                            if device.feature_code == 200 or device.feature_code == "200":
                                continue
                        entity = HisenseSwitch(
                            coordinator,
                            device,
                            switch_type,
                            switch_info
                        )
                        entities.append(entity)

                # Add dynamic zone switches (e.g. t_zone1, t_zone2, ...)
                for switch_type, switch_info in _build_zone_switch_definitions(device, parser):
                    entity = HisenseSwitch(
                        coordinator,
                        device,
                        switch_type,
                        switch_info,
                    )
                    entities.append(entity)

                # Add dehumidifier fan-speed switches.
                if device.type_code == "007":
                    _LOGGER.debug("Processing dehumidifier fan-speed switches for feature %s", device.feature_code)
                    parser = coordinator.api_client.parsers.get(device.device_id)
                    if not parser:
                        _LOGGER.warning(
                            "Skipping dehumidifier fan-speed switches for %s because parser is missing",
                            device.name,
                        )
                        continue
                    _LOGGER.debug("Dehumidifier parser attributes for %s: %s", device.feature_code, parser.attributes)
                    if "t_fan_speed" in parser.attributes:
                        fan_attr = parser.attributes['t_fan_speed']
                        _LOGGER.debug("Dehumidifier fan attribute for %s: %s", device.feature_code,
                                      parser.attributes.get("t_fan_speed"))
                        static_data = coordinator.api_client.static_data.get(device.device_id)
                        if static_data:
                            _LOGGER.debug("Loaded dehumidifier static data for %s: %s", device.feature_code, static_data)
                            # Build per-speed capability flags, defaulting to "0".
                            feature_flags = {
                                "自动": static_data.get("Wind_speed_gear_selection_auto", "0"),
                                "中风": static_data.get("Wind_speed_gear_selection_middle", "0"),
                                "高风": static_data.get("Wind_speed_gear_selection_high", "0"),
                                "低风": static_data.get("Wind_speed_gear_selection_low", "0")
                            }

                            # Map labels back to raw device values.
                            reverse_map = {'低风': '0', '高风': '1', '中风': '3', '自动': '2'}

                            # Create switches only for supported labels.
                            for label in ["自动", "中风", "高风", "低风"]:
                                if feature_flags[label] != "1":
                                    _LOGGER.debug("Device %s does not support %s fan speed, skipping", device.name, label)
                                    continue

                                value_str = reverse_map.get(label)
                                if value_str is None:
                                    _LOGGER.warning("Device %s fan-speed label %s has no raw value, skipping", device.name, label)
                                    continue

                                switch_type = f"fan_speed_{label.lower().replace(' ', '_')}"
                                switch_info = {
                                    "key": fan_attr.key,
                                    "name": f"{label} 风速",
                                    "icon_on": "mdi:fan",
                                    "icon_off": "mdi:fan-off",
                                    "description": f"切换到 {label} 风速",
                                    "expected_value": value_str
                                }
                                entity = HisenseSwitch(
                                    coordinator,
                                    device,
                                    switch_type,
                                    switch_info,
                                    expected_value=value_str
                                )
                                entities.append(entity)
                        else:
                            for value_str, label in fan_attr.value_map.items():
                                _LOGGER.debug("Creating dehumidifier fan-speed switch feature=%s value=%s label=%s", device.feature_code, value_str, label)
                                switch_type = f"fan_speed_{label.lower().replace(' ', '_')}"
                                switch_info = {
                                    "key": fan_attr.key,
                                    "name": f"{label} 风速",
                                    "icon_on": "mdi:fan",
                                    "icon_off": "mdi:fan-off",
                                    "description": f"切换到 {label} 风速",
                                    "expected_value": value_str
                                }
                                entity = HisenseSwitch(
                                    coordinator,
                                    device,
                                    switch_type,
                                    switch_info,
                                    expected_value=value_str
                                )
                                entities.append(entity)

            else:
                _LOGGER.warning(
                    "Skipping unsupported device: %s-%s (%s)",
                    getattr(device, 'type_code', None),
                    getattr(device, 'feature_code', None),
                    getattr(device, 'name', None)
                )

        if not entities:
            _LOGGER.warning("No supported switches found")
            return

        _LOGGER.info("Adding %d switch entities", len(entities))
        async_add_entities(entities)

    except Exception as err:
        _LOGGER.error("Failed to set up switch platform: %s", err)
        raise

class HisenseSwitch(CoordinatorEntity, SwitchEntity):
    """Representation of a Hisense AC switch."""

    _attr_has_entity_name = True
    _debounce_delay = 10

    def __init__(
        self,
        coordinator: HisenseACPluginDataUpdateCoordinator,
        device: HisenseDeviceInfo,
        switch_type: str,
        switch_info: dict,
        expected_value: str = None  # 新增参数
    ) -> None:
        """Initialize the switch entity."""
        super().__init__(coordinator)
        self._last_action_time = 0  # 上次操作时间
        self.device = device
        self.cached = False
        self.feature_code = device.feature_code
        self._switch_info = switch_info
        self._device_id = device.puid
        self._switch_type = switch_type
        self._switch_key = switch_info["key"]
        self._attr_unique_id = f"{device.device_id}_{switch_type}"
        self._attr_name = switch_info["name"]
        self._last_cloud_value = None  # 新增：存储最后一次云端推送的状态值
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.device_id)},
            name=device.name,
            manufacturer="Hisense",
            model=f"{device.type_name} ({device.feature_name})",
        )
        self._attr_icon = switch_info["icon_off"]
        self._attr_entity_registry_enabled_default = True
        self._expected_value = expected_value
        key_lower = self._switch_key.lower()
        if "zone" in key_lower:
            # Keep zone switches in config section to avoid pushing core modes to bottom.
            self._attr_entity_category = EntityCategory.CONFIG

    async def async_added_to_hass(self):
        """Run when entity is added to Home Assistant."""
        await super().async_added_to_hass()
        # Subscribe to state-change events for debounce updates.
        self.async_on_remove(
            async_track_state_change_event(
                self.hass,
                [self.entity_id],
                self._handle_device_state_change
            )
        )

    @callback
    def _handle_device_state_change(self, event: Event) -> None:
        """Handle device state-change event."""
        _LOGGER.debug("Switch state-change event: %s", event.data)
        new_state = event.data.get("new_state")
        if new_state:
            # Schedule state update on HA loop.
            self.hass.add_job(self._async_schedule_update)

    async def _async_schedule_update(self):
        """Schedule entity-state update."""
        self.async_schedule_update_ha_state(True)

    # def _update_entity_name(self):
    #     """根据设备状态动态更新实体名称。"""
    #     hass = self.hass
    #     current_lang = hass.config.language
    #     translations = hass.data.get(f"{DOMAIN}.translations", {}).get(current_lang, {})
    #
    #     # 基础翻译键
    #     translation_key = self._switch_type
    #
    #     # 特殊处理强力模式名称：根据当前模式动态调整翻译键
    #     if self._switch_type == "rapid_mode":
    #         current_mode = self._device.get_status_value(StatusKey.MODE) if self._device else None
    #         _LOGGER.info("当前模式: %s", current_mode)
    #         if current_mode == "2":  # 假设 "1" 对应制冷模式（需根据实际 StatusKey 的值调整）
    #             translation_key = "rapid_mode_cold"
    #         elif current_mode == "1":  # 假设 "2" 对应制热模式（需根据实际 StatusKey 的值调整）
    #             translation_key = "rapid_mode_heat"
    #     _LOGGER.info("翻译键: %s", translation_key)
    #     # 获取翻译后的名称
    #     translated_name = translations.get(translation_key, self._switch_info["name"])
    #     _LOGGER.info("翻译后的名称: %s", translated_name)
    #     self._attr_name = translated_name

    @property
    def name(self) -> str:
        """Return translated entity name."""
        hass = self.hass
        translation_key = self._switch_type
        current_lang = hass.config.language
        translations = hass.data.get(f"{DOMAIN}.translations", {}).get(current_lang, {})
        translated_name = translations.get(translation_key)
        if translated_name:
            return translated_name

        zone_match = re.search(r"zone_(\d+)", translation_key)
        if zone_match:
            zone_idx = zone_match.group(1)
            if current_lang == "zh-Hans":
                return f"分区{zone_idx}"
            return f"Zone {zone_idx}"

        translated_name = self._switch_info["name"]
        return translated_name
    @property
    def _device(self):
        """Get current device data from coordinator."""
        return self.coordinator.get_device(self._device_id)

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        device = self._device
        if not device:
            return False

        parser = self.coordinator.api_client.parsers.get(device.device_id)
        parser_exists = parser is not None
        key_in_status = self._switch_key in (device.status or {})
        key_in_parser = bool(parser_exists and getattr(parser, "attributes", None) and self._switch_key in parser.attributes)
        supported = key_in_status or key_in_parser

        if self._switch_type == "rapid_mode":
            available = device.is_online and supported
            if not available:
                _LOGGER.debug(
                    "Rapid Mode unavailable device=%s t_super=%s parser=%s supported=%s available=%s",
                    device.name,
                    device.get_status_value(StatusKey.RAPID),
                    parser_exists,
                    supported,
                    available,
                )
            return available

        if not device.is_online or not device.is_onOff:
            return False

        # Check if the switch should be hidden based on the current mode
        current_mode = device.get_status_value(StatusKey.MODE)
        if self._switch_type == "quiet_mode":
            if current_mode in ["4", "3"]:  # Assuming "0" is AUTO and "1" is FAN
                return False
        elif self._switch_type == "8heat_mode":
            if current_mode not in ["1"]:
                return False
        elif self._switch_type == "eco_mode":
            if self.device.feature_code == "199":
                if current_mode in ["4", "0"]:
                    return False
            else:
                if current_mode in ["4"]:
                    return False
        elif self.device.type_code == "007" and self._switch_type.startswith("fan_speed_"):
            if current_mode in ["2"]:
                return False
            # Hide dehumidifier fan-speed selector while already active.
            if self.is_on:
                return False

        return True

    @property
    def is_on(self) -> bool:
        """Return true if the switch is on."""
        current_time = time.time()

        # Use cached state during debounce window.
        if current_time - self._last_action_time < self._debounce_delay:
            _LOGGER.debug("Using cached switch state during debounce: %s", self.cached)
            return self.cached

        # Debounce window finished. Fall back to cloud state.
        _LOGGER.debug("Debounce window finished, reading cloud state")
        self.cached = False
        if self.device.type_code == "007" and self._switch_type.startswith("fan_speed_"):
            fan_speed_label = self._switch_type.split("_")[-1]

            # Map fan-speed label to raw value.
            value_map = {
                "自动": "2",
                "中风": "3",
                "高风": "1",
                "低风": "0"
            }
            expected_value = value_map.get(fan_speed_label)

            current_value = self._device.get_status_value("t_fan_speed")

            _LOGGER.debug("Dehumidifier fan-speed compare current=%s expected=%s", current_value, expected_value)

            return current_value == expected_value
        else:
            value = self._device.get_status_value(self._switch_key)
            self._last_cloud_value = value
            return value == "1"

    @property
    def icon(self) -> str:
        """Correctly handle fan speed switch icons"""
        if self._switch_type.startswith("fan_speed_"):
            return self._switch_info["icon_on"] if self.is_on else self._switch_info["icon_off"]
        else:
            switch_info = SWITCH_TYPES.get(self._switch_type, {})
            return switch_info.get("icon_on", "mdi:fan") if self.is_on else switch_info.get("icon_off", "mdi:fan-off")


    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        current_time = time.time()
        self.cached = True
        self._last_action_time = current_time
        self._last_cloud_value = None

        try:
            if self._switch_type.startswith("fan_speed_"):
                value = self._expected_value
            else:
                value = "1"

            await self.coordinator.async_control_device(
                puid=self._device_id,
                properties={self._switch_key: value},
            )

            if self._switch_type.startswith("fan_speed_"):
                fan_speed_key = self._switch_info["key"]
                self._device.status[fan_speed_key] = value
            else:
                self._device.status[self._switch_key] = value

            self._last_action_time = current_time
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("Failed to turn on %s: %s", self._attr_name, err)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        current_time = time.time()
        self.cached = False
        self._last_action_time = current_time
        self._last_cloud_value = None

        try:
            await self.coordinator.async_control_device(
                puid=self._device_id,
                properties={self._switch_key: "0"},
            )

            self._device.status[self._switch_key] = "0"

            self._last_action_time = current_time
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("Failed to turn off %s: %s", self._attr_name, err)

    async def _async_schedule_update(self):
        """Schedule entity update with debounce handling."""
        current_time = time.time()

        # Delay refresh while debounce window is active.
        if current_time - self._last_action_time < self._debounce_delay:
            _LOGGER.debug("Delaying switch update during debounce window")
            remaining_time = self._debounce_delay - (current_time - self._last_action_time)
            self.hass.helpers.dispatcher.async_dispatcher_send(
                f"{DOMAIN}_switch_update_{self.entity_id}",
                remaining_time
            )
        else:
            self.async_schedule_update_ha_state(True)
