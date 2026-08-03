"""Select controls for Hisense ConnectLife devices."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import HisenseACPluginDataUpdateCoordinator
from .models import DeviceInfo as HisenseDeviceInfo

SELECT_TYPES = {
    "sleep_level": (
        "t_sleep",
        {"Off": "0", "General": "1", "Elderly": "2", "Teenagers": "3", "Children": "4"},
    ),
    "temperature_unit": ("t_temp_type", {"Celsius": "0", "Fahrenheit": "1"}),
}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up select entities for supported device properties."""
    coordinator: HisenseACPluginDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    entities = []
    for device in coordinator.data.values():
        if not isinstance(device, HisenseDeviceInfo) or not device.is_devices():
            continue
        parser = coordinator.api_client.parsers.get(device.device_id)
        for select_type, (key, options) in SELECT_TYPES.items():
            if device.has_attribute(key, parser):
                entities.append(HisenseSelect(coordinator, device, select_type, key, options))
    if entities:
        async_add_entities(entities)


class HisenseSelect(CoordinatorEntity, SelectEntity):
    """An enum property exposed by a Hisense device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, device, select_type: str, key: str, option_values: dict[str, str]) -> None:
        super().__init__(coordinator)
        self._device_id = device.puid
        self._key = key
        self._option_values = option_values
        self._attr_unique_id = f"{device.device_id}_{select_type}"
        self._attr_name = select_type.replace("_", " ").title()
        self._attr_options = list(option_values)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.device_id)}, name=device.name,
            manufacturer="Hisense", model=f"{device.type_name} ({device.feature_name})",
        )

    @property
    def _device(self):
        return self.coordinator.get_device(self._device_id)

    @property
    def available(self) -> bool:
        return bool(self._device and self._device.is_online)

    @property
    def current_option(self) -> str | None:
        value = self._device.get_status_value(self._key) if self._device else None
        return next((option for option, raw in self._option_values.items() if raw == value), None)

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_control_device(
            puid=self._device_id, properties={self._key: self._option_values[option]}
        )
