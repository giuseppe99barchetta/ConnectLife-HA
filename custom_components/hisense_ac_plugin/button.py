"""Button controls for Hisense ConnectLife devices."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import HisenseACPluginDataUpdateCoordinator
from .models import DeviceInfo as HisenseDeviceInfo


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up refresh buttons for supported devices."""
    coordinator: HisenseACPluginDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    entities = []
    for device in coordinator.data.values():
        if not isinstance(device, HisenseDeviceInfo) or not device.is_devices():
            continue
        parser = coordinator.api_client.parsers.get(device.device_id)
        if device.has_attribute("t_device_info", parser):
            entities.append(HisenseRefreshButton(coordinator, device))
    if entities:
        async_add_entities(entities)


class HisenseRefreshButton(CoordinatorEntity, ButtonEntity):
    """Request a device-side status refresh."""

    _attr_has_entity_name = True
    _attr_name = "Refresh Device Data"
    _attr_icon = "mdi:refresh"

    def __init__(self, coordinator, device) -> None:
        super().__init__(coordinator)
        self._device_id = device.puid
        self._attr_unique_id = f"{device.device_id}_refresh_device_data"
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

    async def async_press(self) -> None:
        await self.coordinator.async_control_device(
            puid=self._device_id, properties={"t_device_info": "1"}
        )
