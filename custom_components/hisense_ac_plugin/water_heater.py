"""Platform for Hisense Water Heater integration."""
from __future__ import annotations

import logging

from homeassistant.components.water_heater import (
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
    STATE_HEAT_PUMP,
    STATE_OFF,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    MIN_TEMP_WATER,
    MAX_TEMP_WATER,
    StatusKey, MIN_TEMP, MAX_TEMP,
)
from .coordinator import HisenseACPluginDataUpdateCoordinator
from .models import DeviceInfo as HisenseDeviceInfo

_LOGGER = logging.getLogger(__name__)
# Custom operation mode constants.
STATE_DUAL_1 = "STATE_DUAL_1"
STATE_DUAL_MODE = "STATE_DUAL_MODE"
STATE_STANDARD_MODE = "STATE_AUTO"
STATE_ELECTRIC = "STATE_ELECTRIC"
STATE_ECO = "eco_mode"
# Operation mode mapping.
OPERATION_MODE_MAP = {
    "9": STATE_ECO,
    "12": STATE_ELECTRIC,
    "8": STATE_STANDARD_MODE,
    "10": STATE_DUAL_MODE,  # Dual mode.
    "11": STATE_DUAL_1  # Dual mode 1.
}

REVERSE_OPERATION_MAP = {v: k for k, v in OPERATION_MODE_MAP.items()}


async def async_setup_entry(
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Hisense water-heater platform."""
    _LOGGER.debug("Starting water-heater platform setup")
    coordinator: HisenseACPluginDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    try:
        # Trigger initial data update
        await coordinator.async_config_entry_first_refresh()

        # Get devices from coordinator
        devices = coordinator.data
        if not devices:
            _LOGGER.warning("No devices found in coordinator data")
            return

        _LOGGER.debug("Coordinator WaterHeater after refresh: %s", devices)

        entities = [
            HisenseWaterHeater(coordinator, device)
            for device_id, device in devices.items()
            if isinstance(device, HisenseDeviceInfo) and device.is_water()
        ]
        for device_id, device in devices.items():
            _LOGGER.debug("Processing 035: %s", device.to_dict())
            if not isinstance(device, HisenseDeviceInfo) or device.type_code != "035":
                continue
            if device.feature_code == "699":
                _LOGGER.info(
                    "Adding 035 entity for device: %s (type: %s-%s)",
                    device.name,
                    device.type_code,
                    device.feature_code
                )
                entity = Atw035699WaterHeater(coordinator, device)
                entities.append(entity)
            else:
                _LOGGER.warning(
                    "Skipping unsupported water-heater device: %s-%s (%s)",
                    getattr(device, 'type_code', None),
                    getattr(device, 'feature_code', None),
                    getattr(device, 'name', None)
                )
        if entities:
            async_add_entities(entities)
        else:
            _LOGGER.debug("No supported water heaters found")

    except Exception as err:
        _LOGGER.error("Failed to setup water heater platform: %s", err)
        raise


class HisenseWaterHeater(CoordinatorEntity, WaterHeaterEntity):
    """Hisense Water Heater entity implementation."""

    _attr_has_entity_name = False
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = MIN_TEMP
    _attr_max_temp = MAX_TEMP
    _attr_target_temperature_step = 1
    _attr_supported_features = (
            WaterHeaterEntityFeature.TARGET_TEMPERATURE |
            WaterHeaterEntityFeature.OPERATION_MODE |
            WaterHeaterEntityFeature.ON_OFF  # Expose on/off support.
    )

    # Temperature ranges by feature code and normalized HA mode.
    TEMP_RANGE_MAP = {
        "500": {
            STATE_STANDARD_MODE: (15, 60),
            STATE_ECO: (15, 60),
            STATE_DUAL_MODE: (15, 65),
            STATE_DUAL_1: (15, 70),
            STATE_ELECTRIC: (15, 70),
        },
        "501": {
            STATE_STANDARD_MODE: (15, 60),
            STATE_ECO: (15, 60),
            STATE_DUAL_MODE: (15, 65),
            STATE_DUAL_1: (15, 70),
            STATE_ELECTRIC: (15, 70),
        },
        "502": {
            STATE_STANDARD_MODE: (20, 65),
            STATE_ECO: (20, 70),
            STATE_DUAL_1: (20, 75),
            STATE_ELECTRIC: (20, 80),
        },
    }

    def __init__(
            self,
            coordinator: HisenseACPluginDataUpdateCoordinator,
            device: HisenseDeviceInfo,
    ) -> None:
        """Initialize the water heater entity."""
        super().__init__(coordinator)
        self._attr_target_temperature_step = 1
        _LOGGER.debug("Target temperature step set to: %s", self._attr_target_temperature_step)
        self._device_id = device.puid
        self._attr_unique_id = f"{device.device_id}_water_heater"
        self._attr_name = device.name
        self.current_mode = self._get_current_mode(device)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.device_id)},
            name=device.name,
            manufacturer="Hisense",
            model=f"{device.type_name} ({device.feature_name})",
        )
        _LOGGER.debug("Initializing water-heater entity for feature: %s", device.feature_name)
        device_type = device.get_device_type()
        if device_type:
            try:
                self._parser = coordinator.api_client.parsers.get(device.device_id)
                _LOGGER.debug("Using parser for device type %s-%s:%s", device_type.type_code, device_type.feature_code,
                              self._parser)
                # Store type_code and feature_code for later feature checks.
                self._current_type_code = device_type.type_code
                self._current_feature_code = device_type.feature_code
                # Initialize device capabilities.
                self._attr_operation_list = self._get_supported_modes(device)
                # Initialize the temperature range.
                self._update_temperature_range()
            except Exception as err:
                _LOGGER.error("Failed to get device parser: %s", err)
                self._parser = None
        else:
            self._parser = None

        # Default modes if parser not available
        if not hasattr(self, '_attr_operation_list'):
            self._attr_operation_list = [STATE_OFF, STATE_ECO, STATE_ELECTRIC, STATE_STANDARD_MODE, STATE_DUAL_MODE]


    def _get_supported_modes(self, device: HisenseDeviceInfo) -> list[str]:
        """Return the supported operation modes."""
        modes = [STATE_OFF]
        work_mode_attr = self._parser.attributes.get(StatusKey.MODE)
        if work_mode_attr and work_mode_attr.value_map:
            for key, value in work_mode_attr.value_map.items():
                # Map Chinese descriptions to HA modes
                if "ECO模式" in value or "eco" in value.lower():
                    modes.append(STATE_ECO)
                elif "电热热水模式" in value or "electric" in value.lower():
                    modes.append(STATE_ELECTRIC)
                elif "标准模式" in value or "heat_pump" in value.lower():
                    modes.append(STATE_STANDARD_MODE)
                elif "双能热水模式" in value or "high_demand" in value.lower():
                    modes.append(STATE_DUAL_MODE)
                elif "双能1模式" in value or "dual_1" in value.lower():
                    modes.append(STATE_DUAL_1)

        return modes

    def _get_current_mode(self, device: HisenseDeviceInfo | None = None) -> str | None:
        """Return normalized Home Assistant mode for current device state.

        Keep raw Hisense mode values at API boundary only. Entity state and
        temperature-range lookups use HA mode identifiers consistently.
        """
        target_device = device or self._device
        if not target_device:
            return None
        return OPERATION_MODE_MAP.get(target_device.get_status_value(StatusKey.MODE))

    def _update_temperature_range(self):
        """Update the temperature range based on the current mode and feature_code."""
        if not self._parser or not self._current_feature_code:
            return

        current_mode = self._get_current_mode()
        self.current_mode = current_mode
        if current_mode in self.TEMP_RANGE_MAP.get(self._current_feature_code, {}):
            min_temp, max_temp = self.TEMP_RANGE_MAP[self._current_feature_code][current_mode]
            self._attr_min_temp = min_temp
            self._attr_max_temp = max_temp
            _LOGGER.debug("Updated temperature range to %d-%d for mode %s and feature_code %s", min_temp, max_temp,
                          current_mode, self._current_feature_code)
        else:
            _LOGGER.warning("No temperature range found for mode %s and feature_code %s", current_mode,
                            self._current_feature_code)

    @property
    def _device(self):
        """Get the current device state from the coordinator."""
        return self.coordinator.get_device(self._device_id)

    @property
    def available(self) -> bool:
        return self._device and self._device.is_online

    @property
    def is_on(self) -> bool:
        """Return whether the water heater is on."""
        if not self._device:
            return False
        power_status = self._device.get_status_value(StatusKey.POWER)
        return power_status == "1"

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

    # Local helper for translated operation names.
    def _get_translation(self, key: str) -> str:
        current_lang = self.hass.config.language
        translations = self.hass.data.get(f"{DOMAIN}.translations", {}).get(current_lang, {})

        # Feature codes 500/501 expose alternate translated labels.
        if self._current_feature_code == "501" or self._current_feature_code == "500":
            if key == STATE_DUAL_MODE:
                return translations.get("STATE_DUAL_MODE_", key)
            elif key == STATE_DUAL_1:
                return translations.get("STATE_DUAL_1_", key)

        # Otherwise use the default translation key.
        return translations.get(key, key)

    @property
    def current_operation(self) -> str | None:
        """Return the translated current operation mode."""
        if not self._device or not self.is_on:
            return self._get_translation(STATE_OFF)
        hisense_mode = self._device.get_status_value(StatusKey.MODE)
        mode_key = OPERATION_MODE_MAP.get(hisense_mode, STATE_ELECTRIC)

        # Feature 501 uses alternate labels for boost modes.
        if self._current_feature_code == "501":
            if mode_key == STATE_DUAL_MODE:
                return self._get_translation("STATE_DUAL_MODE_")
            elif mode_key == STATE_DUAL_1:
                return self._get_translation("STATE_DUAL_1_")
        _LOGGER.debug("Current heat-pump mode for feature %s: %s", self._current_feature_code, self.current_mode)
        mode = self._get_translation(mode_key)
        if self.current_mode != mode_key:
            self.current_mode = mode_key
            self._update_temperature_range()
            self.schedule_update_ha_state()
        return mode


    @property
    def operation_list(self) -> list[str]:
        """Return the translated supported mode list."""
        _LOGGER.debug("Current supported operation_list: %s", self._attr_operation_list)
        return [self._get_translation(mode) for mode in self._attr_operation_list]
    @property
    def current_temperature(self) -> float | None:
        """Return the current water temperature."""
        if not self._device:
            return None
        temp = self._device.get_status_value(StatusKey.WATER_TANK_TEMP)
        if isinstance(temp, str):
            try:
                return float(temp)
            except ValueError:
                _LOGGER.error("Failed to convert temperature to float: %s", temp)
                return None
        return temp

    @property
    def target_temperature(self) -> float | None:
        """Return the target water temperature."""
        if not self._device:
            return None
        temp = self._device.get_status_value(StatusKey.TARGET_TEMP)
        if isinstance(temp, str):
            try:
                return float(temp)
            except ValueError:
                _LOGGER.error("Failed to convert target temperature to float: %s", temp)
                return None
        return temp

    async def async_set_temperature(self, **kwargs) -> None:
        """Set new target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return

        try:
            await self.coordinator.async_control_device(
                puid=self._device_id,
                properties={StatusKey.TARGET_TEMP: str(int(temperature))},
            )
        except Exception as err:
            _LOGGER.error("Failed to set temperature: %s", err)

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        """Set the water-heater operation mode."""
        if operation_mode == self._get_translation(STATE_OFF):
            await self.async_turn_off()
            return

        try:
            # Handle feature-specific translated labels first.
            special_keys = {
                self._get_translation("STATE_DUAL_MODE_"): STATE_DUAL_MODE,
                self._get_translation("STATE_DUAL_1_"): STATE_DUAL_1,
            }

            if operation_mode in special_keys:
                original_key = special_keys[operation_mode]
            else:
                # Otherwise resolve the generic translation key.
                current_lang = self.hass.config.language
                translations = self.hass.data[f"{DOMAIN}.translations"][current_lang]
                original_key = None
                for k, v in translations.items():
                    if v == operation_mode:
                        original_key = k
                        break

            if not original_key:
                _LOGGER.error("Unable to find translation key for operation mode: %s", operation_mode)
                return

            # Convert the normalized mode key back to the raw Hisense value.
            hisense_mode = REVERSE_OPERATION_MAP.get(original_key)
            if not hisense_mode:
                _LOGGER.error("Unable to find Hisense mode value for key: %s", original_key)
                return

            # Ensure the device is powered on before changing mode.
            if not self.is_on:
                await self.async_turn_on()

            # Send the raw Hisense mode value.
            await self.coordinator.async_control_device(
                puid=self._device_id,
                properties={StatusKey.MODE: hisense_mode},
            )


            # Refresh entity state immediately after the command.
            self.async_write_ha_state()

        except Exception as err:
            _LOGGER.error("Failed to set water-heater mode: %s", err)

    @property
    def extra_state_attributes(self):
        _LOGGER.info("Initializing Daiking Altherma HotWaterTank... %s", self._attr_target_temperature_step)
        """Return the optional device state attributes."""
        data = {"target_temp_step": 1.0}
        return data
    async def async_turn_away_mode_on(self) -> None:
        """Enable away mode."""
        try:
            await self.coordinator.set_away_mode(self._device_id, True)
            self.async_write_ha_state()
        except Exception as err:
            _LOGGER.error("Enable away mode failed: %s", err)

    async def async_turn_away_mode_off(self) -> None:
        """Disable away mode."""
        try:
            await self.coordinator.set_away_mode(self._device_id, False)
            self.async_write_ha_state()
        except Exception as err:
            _LOGGER.error("Disable away mode failed: %s", err)
    @property
    def supported_features(self) -> int:
        """Hide temperature controls when the device is powered off."""
        features = (
                WaterHeaterEntityFeature.TARGET_TEMPERATURE |
                WaterHeaterEntityFeature.OPERATION_MODE |
                WaterHeaterEntityFeature.ON_OFF
        )
        if not self.is_on:
            features &= ~WaterHeaterEntityFeature.TARGET_TEMPERATURE
        return features

STATE_HEAT = "STATE_HEAT"
STATE_COOL = "STATE_COOL"
STATE_AUTO = "STATE_AUTO"
STATE_HOT_WATER_COOL = "STATE_HOT_WATER_COOL"
STATE_HOT_WATER_AUTO = "STATE_HOT_WATER_AUTO"
STATE_HOT_WATER = "STATE_HOT_WATER"
STATE_HOT_WATER_HEAT = "STATE_HOT_WATER_HEAT"

OPERATION1_MODE_MAP = {
    "0": STATE_HEAT,
    "1": STATE_COOL,
    "15": STATE_AUTO,
    "5": STATE_HOT_WATER_COOL,
    "16": STATE_HOT_WATER_AUTO,
    "3": STATE_HOT_WATER,
    "6": STATE_HOT_WATER_HEAT,
}
REVERSE_OPERATION1_MAP = {v: k for k, v in OPERATION1_MODE_MAP.items()}

class Atw035699WaterHeater(CoordinatorEntity, WaterHeaterEntity):
    """Hisense ATW 035-699 Water Heater entity implementation."""

    _attr_has_entity_name = False
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = (
        WaterHeaterEntityFeature.OPERATION_MODE |
        WaterHeaterEntityFeature.ON_OFF
    )

    # Temperature ranges by combined HVAC/DHW operating mode.
    _temperatureRange = [
        # [ModeType.COOL] Cooling
        [16, 30, 5, 27, None, None],
        # [ModeType.HEAT] Heating
        [16, 30, 25, 65, None, None],
        # [ModeType.AUTO] Auto
        [16, 30, 5, 65, None, None],
        # [ModeType.COOL_DHW] Cooling + domestic hot water
        [16, 30, 5, 27, 35, 55],
        # [ModeType.HEAT_DHW] Heating + domestic hot water
        [16, 30, 25, 65, 35, 55],
        # [ModeType.AUTO_DHW] Auto + domestic hot water
        [16, 30, 5, 65, 35, 55],
        # [ModeType.ONLY_DHW] Domestic hot water only
        [None, None, None, None, 35, 55],
    ]

    # Default temperatures by combined HVAC/DHW operating mode.
    _defaultTemperature = [
        # [ModeType.COOL] Cooling
        [7, None, None, 26],
        # [ModeType.HEAT] Heating
        [45, 35, None, 20],
        # [ModeType.AUTO] Auto
        [7, None, None, 26],
        # [ModeType.COOL_DHW] Cooling + domestic hot water
        [7, None, 50, 26],
        # [ModeType.HEAT_DHW] Heating + domestic hot water
        [45, 35, 50, 20],
        # [ModeType.AUTO_DHW] Auto + domestic hot water
        [45, None, 50, 26],
        # [ModeType.ONLY_DHW] Domestic hot water only
        [None, None, 50, None],
    ]

    def __init__(
            self,
            coordinator: HisenseACPluginDataUpdateCoordinator,
            device: HisenseDeviceInfo,
    ) -> None:
        """Initialize the water heater entity."""
        super().__init__(coordinator)
        self._device_id = device.puid
        self._attr_unique_id = f"{device.device_id}_atw_035_699_water_heater"
        self._attr_name = device.name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.device_id)},
            name=device.name,
            manufacturer="Hisense",
            model=f"{device.type_name} ({device.feature_name})",
        )
        _LOGGER.debug("Initializing ATW 035-699 water-heater entity for feature: %s", device.feature_name)
        self.current_mode = self._device.get_status_value(StatusKey.MODE)
        device_type = device.get_device_type()
        if device_type:
            try:
                self._parser = coordinator.api_client.parsers.get(device.device_id)
                _LOGGER.debug("Using parser for device type %s-%s:%s", device_type.type_code, device_type.feature_code,
                              self._parser)
                # Store type_code and feature_code for later feature checks.
                self._current_type_code = device_type.type_code
                self._current_feature_code = device_type.feature_code
                # Initialize device capabilities.
                self._attr_operation_list = self._get_supported_modes(device)
                # Initialize the temperature range.
                self._update_temperature_range()
            except Exception as err:
                _LOGGER.error("Failed to get device parser: %s", err)
                self._parser = None
        else:
            self._parser = None

        # Default modes if parser not available
        if not hasattr(self, '_attr_operation_list'):
            self._attr_operation_list = [STATE_OFF, STATE_ECO, STATE_ELECTRIC, STATE_STANDARD_MODE]

        self._attr_target_temperature_step = 0.5

    def _get_supported_modes(self, device: HisenseDeviceInfo) -> list[str]:
        """Return the supported operation modes."""
        modes = [STATE_OFF]
        work_mode_attr = self._parser.attributes.get(StatusKey.MODE)
        if work_mode_attr and work_mode_attr.value_map:
            for key, value in work_mode_attr.value_map.items():
                # Check compound modes first.
                if "热水+制冷" in value or STATE_HOT_WATER_COOL in value.lower():
                    modes.append(STATE_HOT_WATER_COOL)
                elif "热水+自动" in value or STATE_HOT_WATER_AUTO in value.lower():
                    modes.append(STATE_HOT_WATER_AUTO)
                elif "热水+制热" in value or STATE_HOT_WATER_HEAT in value.lower():
                    modes.append(STATE_HOT_WATER_HEAT)
                elif "热水" in value or STATE_HOT_WATER in value.lower():
                    modes.append(STATE_HOT_WATER)
                # Then check simple modes.
                elif "制热" in value or STATE_HEAT in value.lower():
                    modes.append(STATE_HEAT)
                elif "制冷" in value or STATE_COOL in value.lower():
                    modes.append(STATE_COOL)
                elif "自动" in value or STATE_AUTO in value.lower():
                    modes.append(STATE_AUTO)

        _LOGGER.debug("Resolved ATW 035-699 modes: %s", modes)
        return modes

    def _update_temperature_range(self):
        self._attr_min_temp = MIN_TEMP_WATER
        self._attr_max_temp = MAX_TEMP_WATER
        """Update the temperature range based on the current mode and feature_code."""
        current_mode = self.current_mode
        if current_mode is None:
            return

        # Look up the current mode index.
        mode_index = self._get_mode_index(current_mode)
        if mode_index is None:
            _LOGGER.warning("No temperature range found for mode %s", current_mode)
            return
        _LOGGER.debug("Updating ATW 035-699 temperature limits for mode %s", current_mode)
        # Resolve the temperature range for the current mode.
        temperatures = self._temperatureRange[mode_index]
        if (current_mode == "5"
                or current_mode == "16"
                or current_mode == "3"
                or current_mode == "6"):
            self._attr_min_temp = temperatures[4] if temperatures[4] is not None else MIN_TEMP_WATER
            self._attr_max_temp = temperatures[5] if temperatures[5] is not None else MAX_TEMP_WATER
        else:
            self._attr_min_temp = temperatures[2] if temperatures[2] is not None else MIN_TEMP_WATER
            self._attr_max_temp = temperatures[3] if temperatures[3] is not None else MAX_TEMP_WATER

        _LOGGER.debug("Updated temperature range to %d-%d for mode %s", self._attr_min_temp, self._attr_max_temp, current_mode)

    def _get_mode_index(self, mode: str) -> int | None:
        """Return the internal temperature-range index for a mode."""
        mode_map = {
            "1": 0,
            "0": 1,
            "15": 2,
            "5": 3,
            "16": 4,
            "6": 5,
            "3": 6,
        }
        return mode_map.get(mode)
    @property
    def extra_state_attributes(self):
        data = {"target_temp_step": 0.5}
        return data
    def getTemperatureRangeBasedOnMode(self, mode: str):
        """Return all temperature ranges for the given mode."""
        mode_index = self._get_mode_index(mode)
        if mode_index is None:
            _LOGGER.warning("No temperature range found for mode %s", mode)
            return None

        temperatures = self._temperatureRange[mode_index]
        return {
            "minEnvironmentalTemperature": temperatures[0],
            "maxEnvironmentalTemperature": temperatures[1],
            "minHeatingWaterTemperature": temperatures[2],
            "maxHeatingWaterTemperature": temperatures[3],
            "minDomesticHotWaterTemperature": temperatures[4],
            "maxDomesticHotWaterTemperature": temperatures[5],
        }

    def getDefaultTemperatureValue(self, mode: str, temperature_type: str):
        """Return the default temperature value for a mode/type pair."""
        mode_index = self._get_mode_index(mode)
        if mode_index is None:
            _LOGGER.warning("No default temperature found for mode %s", mode)
            return None

        temperature_map = {
            "zone1": 0,
            "zone2": 1,
            "domesticHotWater": 2,
            "indoorTemperature": 3,
        }
        temperature_index = temperature_map.get(temperature_type)
        if temperature_index is None:
            _LOGGER.warning("No default temperature found for type %s", temperature_type)
            return None

        return self._defaultTemperature[mode_index][temperature_index]

    @property
    def _device(self):
        """Get the current device state from the coordinator."""
        return self.coordinator.get_device(self._device_id)

    @property
    def available(self) -> bool:
        return self._device and self._device.is_online

    @property
    def is_on(self) -> bool:
        """Return whether the water heater is on."""
        if not self._device:
            return False
        power_status = self._device.get_status_value(StatusKey.POWER)
        return power_status == "1"

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

    def _get_translation(self, key: str) -> str:
        current_lang = self.hass.config.language
        translations = self.hass.data.get(f"{DOMAIN}.translations", {}).get(current_lang, {})
        mode = translations.get(key, key)
        # _LOGGER.debug("Current key=%s translations=%s resolved=%s", key, translations, mode)
        return mode

    @property
    def current_operation(self) -> str | None:
        if not self._device or not self.is_on:
            return self._get_translation(STATE_OFF)
        hisense_mode = self._device.get_status_value(StatusKey.MODE)
        mode_key = OPERATION1_MODE_MAP.get(hisense_mode, STATE_ELECTRIC)
        _LOGGER.debug("Current supported current_operation list: %s", self._attr_operation_list)
        mode = self._get_translation(mode_key)
        if self.current_mode != hisense_mode:
            self.current_mode = hisense_mode
            self._update_temperature_range()
            self.schedule_update_ha_state()
        return mode

    @property
    def operation_list(self) -> list[str]:
        """Return the translated supported mode list."""
        _LOGGER.debug("Current supported operation_list: %s", self._attr_operation_list)
        return [self._get_translation(mode) for mode in self._attr_operation_list]

    @property
    def current_temperature(self) -> float | None:
        """Return the current water temperature."""
        if not self._device:
            return None
        temp = self._device.get_status_value(StatusKey.WATER_TANK_TEMP)
        if isinstance(temp, str):
            try:
                return float(temp)
            except ValueError:
                _LOGGER.error("Failed to convert temperature to float: %s", temp)
                return None
        return temp

    @property
    def target_temperature(self) -> float | None:
        _LOGGER.debug("Cloud-reported target temperature: %s", self._device.get_status_value(StatusKey.TARGET_TEMP))
        """Return the target water temperature."""
        if not self._device:
            return None
        temp = self._device.get_status_value(StatusKey.TARGET_TEMP)
        if isinstance(temp, str):
            try:
                return float(temp)
            except ValueError:
                _LOGGER.error("Failed to convert target temperature to float: %s", temp)
                return None
        return temp

    async def async_set_temperature(self, **kwargs) -> None:
        """Set new target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        temp = StatusKey.TARGET_TEMP
        try:
            await self.coordinator.async_control_device(
                puid=self._device_id,
                properties={temp: str(temperature)},
            )
        except Exception as err:
            _LOGGER.error("Failed to set temperature: %s", err)

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        """Set the water-heater operation mode."""
        if operation_mode == self._get_translation(STATE_OFF):
            await self.async_turn_off()
            return

        try:
            # Convert the translated label back to the normalized mode key.
            current_lang = self.hass.config.language
            translations = self.hass.data[f"{DOMAIN}.translations"][current_lang]
            original_key = None
            for k, v in translations.items():
                if v == operation_mode:
                    original_key = k
                    break
            if not original_key:
                _LOGGER.error("Unable to find translation key for operation mode: %s", operation_mode)
                return

            # Convert the normalized key to the raw Hisense mode value.
            hisense_mode = REVERSE_OPERATION1_MAP.get(original_key)
            if not hisense_mode:
                _LOGGER.error("Unable to find Hisense mode value for key: %s", original_key)
                return

            # Ensure the device is powered on before changing mode.
            if not self.is_on:
                await self.async_turn_on()

            # Send the raw Hisense mode value.
            await self.coordinator.async_control_device(
                puid=self._device_id,
                properties={StatusKey.MODE: hisense_mode},
            )


            # Refresh entity state immediately after the command.
            self.async_write_ha_state()

        except Exception as err:
            _LOGGER.error("Failed to set water-heater mode: %s", err)

    async def async_turn_away_mode_on(self) -> None:
        """Enable away mode."""
        try:
            await self.coordinator.set_away_mode(self._device_id, True)
            self.async_write_ha_state()
        except Exception as err:
            _LOGGER.error("Enable away mode failed: %s", err)

    async def async_turn_away_mode_off(self) -> None:
        """Disable away mode."""
        try:
            await self.coordinator.set_away_mode(self._device_id, False)
            self.async_write_ha_state()
        except Exception as err:
            _LOGGER.error("Disable away mode failed: %s", err)

    @property
    def temperatureRange(self):
        """Return the temperature range for the current mode."""
        return self.getTemperatureRangeBasedOnMode(self.current_operation)

    @property
    def supported_features(self) -> int:
        features = (
                WaterHeaterEntityFeature.TARGET_TEMPERATURE |
                WaterHeaterEntityFeature.OPERATION_MODE |
                WaterHeaterEntityFeature.ON_OFF
        )

        # current_operation already exposes the translated active mode name.
        current_mode_translated = self.current_operation
        if (current_mode_translated == self._get_translation(STATE_AUTO)
                or current_mode_translated == self._get_translation(STATE_HEAT)
                or current_mode_translated == self._get_translation(STATE_OFF)
                or current_mode_translated == self._get_translation(STATE_COOL)):
            features &= ~WaterHeaterEntityFeature.TARGET_TEMPERATURE

        return features
