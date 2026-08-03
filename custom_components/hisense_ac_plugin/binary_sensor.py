"""Fault binary sensors for Hisense ConnectLife devices."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import HisenseACPluginDataUpdateCoordinator
from .models import DeviceInfo as HisenseDeviceInfo
from .sensor import FAULT_SENSOR_TYPES


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up persistent diagnostic fault entities."""
    coordinator: HisenseACPluginDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    entities = []
    for device in coordinator.data.values():
        if not isinstance(device, HisenseDeviceInfo) or not device.is_devices():
            continue
        parser = coordinator.api_client.parsers.get(device.device_id)
        for sensor_type, sensor_info in FAULT_SENSOR_TYPES.items():
            key = sensor_info["key"]
            if device.has_attribute(key, parser) or key in device.failed_data:
                entities.append(HisenseFaultBinarySensor(coordinator, device, sensor_type, sensor_info))
    if entities:
        async_add_entities(entities)


class HisenseFaultBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """A device fault that remains available when the fault clears."""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, device, sensor_type: str, sensor_info: dict) -> None:
        super().__init__(coordinator)
        self._device_id = device.device_id
        self._key = sensor_info["key"]
        self._attr_unique_id = f"{device.device_id}_{sensor_type}"
        self._attr_name = sensor_info["name"]
        self._attr_icon = sensor_info["icon"]
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
    def is_on(self) -> bool:
        device = self._device
        return bool(device and (self._key in device.failed_data or device.get_status_value(self._key) == "1"))
