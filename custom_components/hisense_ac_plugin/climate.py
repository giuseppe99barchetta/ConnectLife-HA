"""Platform for Hisense AC climate integration."""
from __future__ import annotations

import logging
import time
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.components.climate.const import (
    SWING_OFF,
    SWING_VERTICAL,
    SWING_BOTH,
    SWING_HORIZONTAL,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_TEMPERATURE,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    MIN_TEMP,
    MAX_TEMP,
    StatusKey,
    FAN_AUTO,
    FAN_ULTRA_LOW,
    FAN_LOW,
    FAN_MEDIUM,
    FAN_HIGH,
    FAN_ULTRA_HIGH,
)
from .coordinator import HisenseACPluginDataUpdateCoordinator
from .api import HisenseApiClient
from .models import DeviceInfo as HisenseDeviceInfo
from .devices import get_device_parser

_LOGGER = logging.getLogger(__name__)

# Standard mappings for Home Assistant HVAC modes
HA_MODE_TO_STR = {
    HVACMode.AUTO: "auto",
    HVACMode.COOL: "cool",
    HVACMode.HEAT: "heat",
    HVACMode.DRY: "dry",
    HVACMode.FAN_ONLY: "fan_only",
    HVACMode.OFF: "off",
}

# Reverse mapping
STR_TO_HA_MODE = {v: k for k, v in HA_MODE_TO_STR.items()}

RAW_FAN_SPEED_FALLBACK_KEY = "t_fan_speed_s"
HORIZONTAL_SWING_KEYS = ("t_left_right", "t_lr", "t_swing_lr", "t_l_r")

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Hisense AC climate platform."""
    coordinator: HisenseACPluginDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    try:
        # Trigger initial data update
        await coordinator.async_config_entry_first_refresh()

        # Get devices from coordinator
        devices = coordinator.data
        _LOGGER.debug("Coordinator data after refresh: %s", devices)

        if not devices:
            _LOGGER.warning("No devices found in coordinator data")
            return

        entities = []
        for device_id, device in devices.items():
            _LOGGER.debug("Processing device: %s", device.to_dict())
            if isinstance(device, HisenseDeviceInfo) and device.is_air_conditioner():
                _LOGGER.info(
                    "Adding climate entity for device: %s (type: %s-%s)",
                    device.name,
                    device.type_code,
                    device.feature_code
                )
                entity = HisenseClimate(coordinator, device)
                entities.append(entity)
            else:
                _LOGGER.warning(
                    "Skipping unsupported device: %s-%s (%s)",
                    getattr(device, 'type_code', None),
                    getattr(device, 'feature_code', None),
                    getattr(device, 'name', None)
                )

        if not entities:
            _LOGGER.warning("No supported devices found")
            return

        _LOGGER.info("Adding %d climate entities", len(entities))
        async_add_entities(entities)

    except Exception as err:
        _LOGGER.error("Failed to set up climate platform: %s", err)
        raise

class HisenseClimate(CoordinatorEntity, ClimateEntity):
    """Hisense AC climate entity."""

    _attr_has_entity_name = False
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = MIN_TEMP
    _attr_max_temp = MAX_TEMP
    _attr_target_temperature_step = 1
    _attr_supported_features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.FAN_MODE
            | ClimateEntityFeature.SWING_MODE
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
        # | ClimateEntityFeature.PRESET_MODE  # Preset mode support can be added later.
    )

    def __init__(
            self,
            coordinator: HisenseACPluginDataUpdateCoordinator,
            device: HisenseDeviceInfo,
    ) -> None:
        """Initialize the climate entity."""
        super().__init__(coordinator)
        self._device_id = device.puid
        self._attr_unique_id = f"{device.device_id}_climate"
        self._attr_name = device.name
        self.hasAuto = False
        self._last_command_time = 0
        self.wait_time = 3
        self._cached_target_temp = None
        self._cached_hvac_mode = HVACMode.OFF
        self._cached_fan_mode = None
        self._cached_swing_mode = SWING_OFF
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.device_id)},
            name=device.name,
            manufacturer="Hisense",
            model=f"{device.type_name} ({device.feature_name})",
        )
        if device.feature_code == '19901':
            self._attr_target_temperature_step = 0.5
        # Get device parser to determine available modes and options
        device_type = device.get_device_type()
        if device_type:
            try:
                self._parser = coordinator.api_client.parsers.get(device.device_id)
                self.static_data = coordinator.api_client.static_data.get(device.device_id)
                _LOGGER.debug("Using parser for device type %s-%s:%s", device_type.type_code, device_type.feature_code,
                              self._parser)
                # Store type_code and feature_code for later feature checks.
                self._current_type_code = device_type.type_code
                self._current_feature_code = device_type.feature_code
                # Set available modes based on device capabilities
                self._setup_hvac_modes()
                self._setup_fan_modes()
                self._setup_swing_modes()
                # Preset modes.
                # self._attr_preset_mode = None
                # self._attr_preset_modes = ["eco", "away", "comfort", "sleep"]
            except Exception as err:
                _LOGGER.error("Failed to get device parser: %s", err)
                self._parser = None
        else:
            self._parser = None

        # Default modes if parser not available
        if not hasattr(self, '_attr_hvac_modes'):
            self._attr_hvac_modes = [
                HVACMode.OFF,
                HVACMode.AUTO,
                HVACMode.COOL,
                HVACMode.HEAT,
                HVACMode.DRY,
                HVACMode.FAN_ONLY,
            ]

        # Read target temperature metadata from the parser.
        target_temp_attr = self._parser.attributes.get(StatusKey.TARGET_TEMP) if self._parser else None

        # Parse propertyValueList into one or more temperature ranges.
        def parse_temperature_range(property_value_list):
            ranges = []
            for item in property_value_list.split(','):
                item = item.strip()
                if '~' in item:
                    lower, upper = map(int, item.split('~'))
                    ranges.append((lower, upper))
            return ranges

        # Use parsed ranges when available, otherwise fall back to defaults.
        if target_temp_attr and target_temp_attr.value_range:
            temperature_ranges = parse_temperature_range(target_temp_attr.value_range)
        else:
            _LOGGER.debug(
                "Target temperature attribute or value range not found for %s-%s, using default range",
                device.type_code,
                device.feature_code,
            )
            temperature_ranges = [(MIN_TEMP, MAX_TEMP)]

        # Inspect the device temperature unit selector.
        temp_type_attr = device.status.get(StatusKey.T_TEMP_TYPE)


        # Pick the correct range based on the reported temperature unit.
        if temp_type_attr != "1":
            # Use the Celsius range.
            if temperature_ranges:
                min_temp, max_temp = temperature_ranges[0]
            else:
                min_temp = MIN_TEMP
                max_temp = MAX_TEMP
            self._attr_temperature_unit = UnitOfTemperature.CELSIUS
        else:
            # Use the Fahrenheit range.
            if len(temperature_ranges) > 1:
                min_temp, max_temp = temperature_ranges[1]
            else:
                min_temp = MIN_TEMP
                max_temp = MAX_TEMP
            self._attr_temperature_unit = UnitOfTemperature.FAHRENHEIT
        # Apply the resolved limits to the entity.
        self._attr_min_temp = min_temp
        self._attr_max_temp = max_temp
        _LOGGER.debug("Resolved temperature limits for %s-%s: %s-%s", device_type.type_code, device_type.feature_code,
                      self._attr_min_temp,self._attr_max_temp)
        if not hasattr(self, '_attr_fan_modes'):
            self._attr_fan_modes = [FAN_AUTO, FAN_ULTRA_LOW, FAN_LOW, FAN_MEDIUM, FAN_HIGH, FAN_ULTRA_HIGH]

        if not hasattr(self, '_attr_swing_modes'):
            self._attr_swing_modes = [SWING_OFF, SWING_VERTICAL]

    def _get_horizontal_swing_key(self) -> str | None:
        """Return the LR swing key exposed by this device, if any."""
        parser_attrs = getattr(self._parser, "attributes", {}) if hasattr(self, "_parser") and self._parser else {}

        for key in HORIZONTAL_SWING_KEYS:
            if key in parser_attrs:
                return key

        device_status = self._device.status if self._device else {}
        for key in HORIZONTAL_SWING_KEYS:
            if key in device_status:
                return key

        return None

    # async def async_set_preset_mode(self, preset_mode: str) -> None:
    #     """Set the preset mode."""
    #     if preset_mode not in self.preset_modes:
    #         raise ValueError(f"Invalid preset mode: {preset_mode}")
    #     self._attr_preset_mode = preset_mode
    #     self.async_write_ha_state()

    def _setup_hvac_modes(self):
        """Set up available HVAC modes based on device capabilities."""
        if not self._parser:
            return

        # Always include OFF mode
        modes = [HVACMode.OFF]
        available_modes = []
        has_heat = '1'
        if self.static_data :
            has_heat = self.static_data.get("Mode_settings")
        # Get work mode attribute from parser
        work_mode_attr = self._parser.attributes.get(StatusKey.MODE)
        if work_mode_attr and work_mode_attr.value_map:
            for key, value in work_mode_attr.value_map.items():
                hvac_mode = self._map_hisense_mode_description(value)
                if hvac_mode is None:
                    continue
                if hvac_mode == HVACMode.HEAT and has_heat != '1':
                    continue
                available_modes.append(hvac_mode)

        # Keep a stable HVAC mode order while skipping unsupported modes.
        desired_order = [HVACMode.COOL, HVACMode.HEAT, HVACMode.DRY, HVACMode.FAN_ONLY, HVACMode.AUTO]
        for mode in desired_order:
            if mode in available_modes:
                modes.append(mode)
        self._attr_hvac_modes = modes

    def _setup_fan_modes(self):
        """Set up available fan modes based on device capabilities."""
        if not self._parser:
            return
        position6_damper_control = '9'
        if self.static_data:
            position6_damper_control = self.static_data.get("Wind_speed_gear_selection")
        fan_modes = []
        fan_speed_attr = self._parser.attributes.get(StatusKey.FAN_SPEED)
        if fan_speed_attr and fan_speed_attr.value_map:
            for key, value in fan_speed_attr.value_map.items():
                normalized_mode = self._map_hisense_fan_description(value)
                if normalized_mode == FAN_AUTO:
                    self.hasAuto = True
                fan_modes.append(normalized_mode or value)

        if fan_modes:
            # Filter unsupported fan tiers based on static capabilities.
            if position6_damper_control != '9':
                # Some devices expose only a reduced fan-speed set.
                fan_modes = [
                    mode for mode in fan_modes
                    if mode not in (FAN_ULTRA_LOW, FAN_ULTRA_HIGH)
                ]
            self._attr_fan_modes = list(dict.fromkeys(fan_modes))

    def _setup_swing_modes(self):
        """Set up available swing modes based on device capabilities."""
        if not self._parser:
            return
        left_and_right = '1'
        upper_and_lower = '1'
        if self.static_data:
            left_and_right = self.static_data.get("Left_and_right_damper_control")
            upper_and_lower = self.static_data.get("Upper_and_lower_damper_control")
        swing_modes = [SWING_OFF]
        vertical_swing_key = StatusKey.SWING

        # Check for vertical swing support (t_up_down)
        vertical_swing_attr = self._parser.attributes.get(StatusKey.SWING)
        if vertical_swing_attr and vertical_swing_attr.value_map:
            if upper_and_lower == '1':
                swing_modes.append(SWING_VERTICAL)

        # Feature 199 only supports vertical swing.
        if self._current_feature_code == '199':
            self._attr_swing_modes = swing_modes
            _LOGGER.debug("Device %s only supports vertical swing (SWING_VERTICAL)", self._device_id)
            return

        # Otherwise continue checking horizontal swing.
        horizontal_swing_key = self._get_horizontal_swing_key()
        horizontal_swing_attr = self._parser.attributes.get(horizontal_swing_key) if horizontal_swing_key else None
        if horizontal_swing_attr and horizontal_swing_attr.value_map:
            if SWING_VERTICAL in swing_modes:
                if left_and_right == '1':
                    swing_modes.append(SWING_HORIZONTAL)
                    swing_modes.append(SWING_BOTH)
            else:
                if left_and_right == '1':
                    swing_modes.append(SWING_HORIZONTAL)

        self._attr_swing_modes = swing_modes
        if self._current_type_code == "009" and self._current_feature_code == "128":
            status_keys = sorted(
                key for key in (self._device.status or {})
                if any(token in str(key).lower() for token in ("left", "right", "lr", "swing", "up_down"))
            )
            parser_keys = sorted(
                key for key in self._parser.attributes
                if any(token in str(key).lower() for token in ("left", "right", "lr", "swing", "up_down"))
            )
            _LOGGER.debug(
                "009-128 swing discovery device=%s status_keys=%s parser_keys=%s vertical_key=%s horizontal_key=%s swing_modes=%s",
                self._attr_name,
                status_keys,
                parser_keys,
                vertical_swing_key,
                horizontal_swing_key,
                swing_modes,
            )
        _LOGGER.debug("Available swing modes: %s", swing_modes)

    @staticmethod
    def _map_hisense_mode_description(description: str) -> HVACMode | None:
        """Map a Hisense work-mode description to a Home Assistant HVAC mode."""
        if not description:
            return None

        normalized = description.strip().lower()
        if "送风" in description or normalized in {"fan", "fan_only", "fan only"}:
            return HVACMode.FAN_ONLY
        if "制冷" in description or normalized == "cool":
            return HVACMode.COOL
        if "制热" in description or normalized == "heat":
            return HVACMode.HEAT
        if "除湿" in description or normalized == "dry":
            return HVACMode.DRY
        if "自动" in description or normalized == "auto":
            return HVACMode.AUTO
        return None

    @staticmethod
    def _map_hisense_fan_description(description: str) -> str | None:
        """Map a Hisense fan-speed description to a stable HA fan mode string."""
        if not description:
            return None

        normalized = description.strip().lower()
        exact_map = {
            "自动": FAN_AUTO,
            "auto": FAN_AUTO,
            "低风": FAN_LOW,
            "低": FAN_LOW,
            "low": FAN_LOW,
            "中风": FAN_MEDIUM,
            "中": FAN_MEDIUM,
            "medium": FAN_MEDIUM,
            "med": FAN_MEDIUM,
            "高风": FAN_HIGH,
            "高": FAN_HIGH,
            "high": FAN_HIGH,
            "中低": FAN_ULTRA_LOW,
            "超低": FAN_ULTRA_LOW,
            "ultra low": FAN_ULTRA_LOW,
            "中高": FAN_ULTRA_HIGH,
            "超高": FAN_ULTRA_HIGH,
            "ultra high": FAN_ULTRA_HIGH,
        }
        return exact_map.get(normalized)

    def _find_hisense_mode_value(self, hvac_mode: HVACMode) -> str | None:
        """Resolve a HA HVAC mode to the raw Hisense mode value."""
        if not hasattr(self, "_parser") or not self._parser:
            return None

        work_mode_attr = self._parser.attributes.get(StatusKey.MODE)
        if not (work_mode_attr and work_mode_attr.value_map):
            return None

        for key, value in work_mode_attr.value_map.items():
            if self._map_hisense_mode_description(value) == hvac_mode:
                return key
        return None

    def _find_hisense_fan_value(self, fan_mode: str) -> str | None:
        """Resolve a HA fan mode to the raw Hisense fan-speed value."""
        if not hasattr(self, "_parser") or not self._parser:
            return None

        fan_attr = self._parser.attributes.get(StatusKey.FAN_SPEED)
        if not (fan_attr and fan_attr.value_map):
            return None

        for key, value in fan_attr.value_map.items():
            normalized_mode = self._map_hisense_fan_description(value)
            if normalized_mode == fan_mode or value == fan_mode:
                return key
        return None

    @property
    def _device(self):
        """Get current device data from coordinator."""
        return self.coordinator.get_device(self._device_id)

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        device = self._device
        parser_exists = hasattr(self, "_parser") and self._parser is not None

        if device is None:
            _LOGGER.debug(
                "Climate availability false: device missing from coordinator device_id=%s parser_exists=%s",
                self._device_id,
                parser_exists,
            )
            return False

        availability = device.is_online
        if not availability:
            _LOGGER.debug(
                "Climate unavailable device=%s type=%s-%s offlineState=%s normalized_online=%s parser_exists=%s status_keys=%s",
                device.name,
                device.type_code,
                device.feature_code,
                device.offline_state,
                device.is_online,
                parser_exists,
                sorted(device.status.keys()),
            )
        return availability

    @property
    def current_temperature(self) -> float | None:
        """Return the current temperature."""
        if not self._device:
            return None
        temp = self._device.get_status_value(StatusKey.TEMPERATURE)
        return float(temp) if temp else None

    @property
    def target_temperature(self) -> float | None:
        """Return the target temperature."""
        if not self._device:
            return None
        temp = self._device.get_status_value(StatusKey.TARGET_TEMP)
        return float(temp) if temp else None

    @property
    def hvac_mode(self) -> HVACMode:
        if time.time() - self._last_command_time < self.wait_time:
            return self._cached_hvac_mode
        """Return hvac operation mode."""
        if not self._device:
            return HVACMode.OFF

        power = self._device.get_status_value(StatusKey.POWER)
        if not power or power == "0":
            return HVACMode.OFF

        mode = self._device.get_status_value(StatusKey.MODE)
        if not mode:
            return HVACMode.AUTO  # Default to AUTO if mode is not set
        _LOGGER.debug("Current device feature=%s mode=%s", self._current_feature_code, mode)
        # Try to map the mode using the device parser
        if hasattr(self, '_parser') and self._parser:
            work_mode_attr = self._parser.attributes.get(StatusKey.MODE)
            if work_mode_attr and work_mode_attr.value_map and mode in work_mode_attr.value_map:
                mode_desc = work_mode_attr.value_map[mode]
                _LOGGER.debug("Mode %s maps to description: %s", mode, mode_desc)
                mapped_mode = self._map_hisense_mode_description(mode_desc)
                if mapped_mode is not None:
                    return mapped_mode

        # Fallback to standard mapping
        ha_mode = STR_TO_HA_MODE.get(mode)
        return HVACMode(ha_mode) if ha_mode else HVACMode.AUTO

    @property
    def fan_mode(self) -> str | None:
        if time.time() - self._last_command_time < self.wait_time and self._cached_fan_mode is not None:
            return self._cached_fan_mode
        """Return the fan setting."""
        if not self._device:
            return None

        fan_mode = self._device.get_status_value(StatusKey.FAN_SPEED)
        if not fan_mode:
            fan_mode = self._device.get_status_value(RAW_FAN_SPEED_FALLBACK_KEY)
        if not fan_mode:
            return FAN_AUTO  # Default to auto

        # Try to map using device parser
        if hasattr(self, '_parser') and self._parser:
            fan_attr = self._parser.attributes.get(StatusKey.FAN_SPEED)
            if fan_attr and fan_attr.value_map and fan_mode in fan_attr.value_map:
                fan_desc = fan_attr.value_map[fan_mode]
                _LOGGER.debug("Fan mode %s maps to fan: %s", fan_mode, fan_desc)
                mapped_fan_mode = self._map_hisense_fan_description(fan_desc)
                if mapped_fan_mode is not None:
                    return mapped_fan_mode
                return fan_desc

        # Fallback to the raw value
        return fan_mode

    @property
    def fan_modes(self):
        modes = list(self._attr_fan_modes)
        if self.hvac_mode == HVACMode.FAN_ONLY:
            if FAN_AUTO in modes:
                modes.remove(FAN_AUTO)
        else:
            if FAN_AUTO not in modes and self.hasAuto:
                modes.append(FAN_AUTO)
        return modes
    @property
    def swing_mode(self) -> str | None:
        if time.time() - self._last_command_time < self.wait_time and self._cached_swing_mode is not None:
            return self._cached_swing_mode
        """Return the swing setting."""
        if not self._device:
            return None
        _LOGGER.debug("Evaluating swing mode for feature=%s status=%s", self._current_feature_code, self._device.status)
        # Get vertical swing status
        vertical_swing = self._device.get_status_value(StatusKey.SWING)

        # Get horizontal swing status
        horizontal_swing_key = self._get_horizontal_swing_key()
        horizontal_swing = self._device.get_status_value(horizontal_swing_key) if horizontal_swing_key else None
        # Feature 199 does not support horizontal swing.
        if self._current_feature_code == '199':
            horizontal_swing = None
        # Determine swing mode based on vertical and horizontal settings
        if (not vertical_swing or vertical_swing == "0") and (not horizontal_swing or horizontal_swing == "0"):
            return SWING_OFF
        elif vertical_swing == "1" and (not horizontal_swing or horizontal_swing == "0"):
            return SWING_VERTICAL
        elif (not vertical_swing or vertical_swing == "0") and horizontal_swing == "1":
            return SWING_HORIZONTAL
        elif vertical_swing == "1" and horizontal_swing == "1":
            return SWING_BOTH

        # Default to off if we can't determine the mode
        return SWING_OFF

    @property
    def supported_features(self) -> int:
        """Return the list of supported features."""
        features = (
                ClimateEntityFeature.TARGET_TEMPERATURE
                | ClimateEntityFeature.FAN_MODE
                | ClimateEntityFeature.SWING_MODE
                | ClimateEntityFeature.TURN_ON
                | ClimateEntityFeature.TURN_OFF
        )

        # Only type 009 devices expose swing controls.
        if self._current_type_code != '009':
            features &= ~ClimateEntityFeature.SWING_MODE

        # Hide swing controls when no effective swing options exist.
        if len(self._attr_swing_modes) <= 1:
            features &= ~ClimateEntityFeature.SWING_MODE
        # Limit target-temperature control to heating and cooling modes.
        current_mode = self.hvac_mode
        if current_mode not in [HVACMode.COOL, HVACMode.HEAT]:
            features &= ~ClimateEntityFeature.TARGET_TEMPERATURE

        # Hide fan control while in dry mode.
        if current_mode == HVACMode.DRY:
            features &= ~ClimateEntityFeature.FAN_MODE

        # Optionally hide fan controls in fan-only mode.
        # if current_mode == HVACMode.FAN_ONLY:
        #     features &= ~ClimateEntityFeature.FAN_MODE

        return features

    async def async_set_temperature(self, **kwargs) -> None:
        """Set new target temperature."""
        # Reject temperature writes for modes that do not support them.
        current_mode = self.hvac_mode
        if current_mode in [HVACMode.FAN_ONLY, HVACMode.DRY, HVACMode.AUTO]:
            _LOGGER.debug("Temperature setting is not allowed in current mode: %s", current_mode)
            return

        temperature = kwargs.get(ATTR_TEMPERATURE)
        _LOGGER.debug("Sending target temperature update kwargs=%s temperature=%s", kwargs, temperature)
        if temperature is None:
            return

        try:
            await self.coordinator.async_control_device(
                puid=self._device_id,
                properties={StatusKey.TARGET_TEMP: str(temperature)},
            )
        except Exception as err:
            _LOGGER.error("Failed to set temperature: %s", err)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        # Cache the requested state briefly to avoid UI bounce after commands.
        self._cached_hvac_mode = hvac_mode
        self._cached_fan_mode = self.fan_mode
        self._cached_swing_mode = self.swing_mode
        self._cached_target_temp = self.target_temperature
        self._last_command_time = time.time()
        self.async_write_ha_state()
        """Set new target hvac mode."""
        if hvac_mode == HVACMode.OFF:
            await self.async_turn_off()
            return

        try:
            # Make sure the device is on first
            power = self._device.get_status_value(StatusKey.POWER)
            if not power or power == "0":
                await self.async_turn_on()

            # Find the Hisense mode value for this HA mode
            hisense_mode = None

            # Try to map using device parser
            if hasattr(self, '_parser') and self._parser:
                hisense_mode = self._find_hisense_mode_value(hvac_mode)

            # Fallback to standard mapping
            if not hisense_mode:
                mode_str = HA_MODE_TO_STR.get(hvac_mode)
                if mode_str:
                    hisense_mode = mode_str
            if hvac_mode != HVACMode.OFF:
                power = self._device.get_status_value(StatusKey.POWER)
                if power == "0":
                    await self.coordinator.async_control_device(
                        puid=self._device_id,
                        properties={
                            StatusKey.POWER: "1",  # Power on first.
                            StatusKey.MODE: hisense_mode  # Keep mode in sync.
                        }
                    )
                    return
            if hisense_mode:
                _LOGGER.debug("Setting HVAC mode to %s (Hisense value: %s)", hvac_mode, hisense_mode)
                await self.coordinator.async_control_device(
                    puid=self._device_id,
                    properties={StatusKey.MODE: hisense_mode},
                )
            else:
                _LOGGER.error("Could not find Hisense mode value for HA mode: %s", hvac_mode)
        except Exception as err:
            _LOGGER.error("Failed to set hvac mode: %s", err)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        # Cache the requested state briefly to avoid UI bounce after commands.
        self._cached_fan_mode = fan_mode
        self._cached_hvac_mode = self.hvac_mode
        self._cached_swing_mode = self.swing_mode
        self._cached_target_temp = self.target_temperature
        self._last_command_time = time.time()
        self.async_write_ha_state()
        """Set new target fan mode."""
        try:
            # Find the Hisense fan mode value for this HA fan mode
            hisense_fan_mode = None

            # Try to map using device parser
            if hasattr(self, '_parser') and self._parser:
                hisense_fan_mode = self._find_hisense_fan_value(fan_mode)

            # Fallback to the fan mode as is
            if not hisense_fan_mode:
                hisense_fan_mode = fan_mode

            _LOGGER.debug("Setting fan mode to %s (Hisense value: %s)", fan_mode, hisense_fan_mode)
            await self.coordinator.async_control_device(
                puid=self._device_id,
                properties={StatusKey.FAN_SPEED: hisense_fan_mode},
            )
        except Exception as err:
            _LOGGER.error("Failed to set fan mode: %s", err)

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        # Cache the requested state briefly to avoid UI bounce after commands.
        self._cached_swing_mode = swing_mode
        self._cached_hvac_mode = self.hvac_mode
        self._cached_fan_mode = self.fan_mode
        self._cached_target_temp = self.target_temperature
        self._last_command_time = time.time()
        self.async_write_ha_state()
        """Set new target swing operation."""
        try:
            properties = {}
            horizontal_swing_key = self._get_horizontal_swing_key()

            # Determine vertical and horizontal swing settings based on mode
            if swing_mode == SWING_OFF:
                properties[StatusKey.SWING] = "0"
                if horizontal_swing_key:
                    properties[horizontal_swing_key] = "0"
            elif swing_mode == SWING_VERTICAL:
                properties[StatusKey.SWING] = "1"
                if horizontal_swing_key:
                    properties[horizontal_swing_key] = "0"
            elif swing_mode == SWING_HORIZONTAL:
                properties[StatusKey.SWING] = "0"
                if horizontal_swing_key:
                    properties[horizontal_swing_key] = "1"
            elif swing_mode == SWING_BOTH:
                properties[StatusKey.SWING] = "1"
                if horizontal_swing_key:
                    properties[horizontal_swing_key] = "1"

            # Check which properties are supported by the device
            if hasattr(self, '_parser') and self._parser:
                # Only include properties that are supported by the device
                supported_properties = {}

                if StatusKey.SWING in properties and self._parser.attributes.get(StatusKey.SWING):
                    supported_properties[StatusKey.SWING] = properties[StatusKey.SWING]

                if (
                    horizontal_swing_key
                    and horizontal_swing_key in properties
                    and self._parser.attributes.get(horizontal_swing_key)
                ):
                    supported_properties[horizontal_swing_key] = properties[horizontal_swing_key]

                # Send the command if we have supported properties
                if supported_properties:
                    _LOGGER.debug("Setting swing mode to %s with properties: %s", swing_mode, supported_properties)
                    await self.coordinator.async_control_device(
                        puid=self._device_id,
                        properties=supported_properties,
                    )

        except Exception as err:
            _LOGGER.error("Failed to set swing mode: %s", err)

    async def async_turn_on(self) -> None:
        """Turn the entity on."""
        try:
            _LOGGER.debug("Turning on device %s", self._device_id)
            await self.coordinator.async_control_device(
                puid=self._device_id,
                properties={StatusKey.POWER: "1"},
            )
        except Exception as err:
            _LOGGER.error("Failed to turn on: %s", err)

    async def async_turn_off(self) -> None:
        """Turn the entity off."""
        try:
            _LOGGER.debug("Turning off device %s", self._device_id)
            await self.coordinator.async_control_device(
                puid=self._device_id,
                properties={StatusKey.POWER: "0"},
            )
        except Exception as err:
            _LOGGER.error("Failed to turn off: %s", err)
    def _handle_coordinator_update(self) -> None:
        device = self.coordinator.get_device(self._device_id)
        if not device:
            _LOGGER.warning("Device %s not found during sensor update", self._device_id)
            return
        """Process coordinator updates only after the command debounce window."""
        if time.time() - self._last_command_time >= self.wait_time:
            super()._handle_coordinator_update()
