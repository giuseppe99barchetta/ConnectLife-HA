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
from .const import StatusKey


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up command buttons for supported devices."""
    coordinator: HisenseACPluginDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    entities = []
    for device in coordinator.data.values():
        if not isinstance(device, HisenseDeviceInfo) or not device.is_devices():
            continue
        parser = coordinator.api_client.parsers.get(device.device_id)
        if device.has_attribute("t_device_info", parser):
            entities.append(HisenseButton(coordinator, device, "refresh"))
        # t_dimmer is absent from the 009-128 devices' advertised controls.
        # Do not expose speculative display buttons: the cloud rejects them.
        dimmer_attr = (parser.attributes.get("t_dimmer") if parser else None)
        if (
            device.type_code == "009"
            and device.feature_code == "128"
            and dimmer_attr is not None
            and "W" in str(getattr(dimmer_attr, "read_write", "")).upper()
        ):
            entities.extend(
                HisenseButton(coordinator, device, button_type)
                for button_type in ("display_on", "display_off")
            )
    if entities:
        async_add_entities(entities)


class HisenseButton(CoordinatorEntity, ButtonEntity):
    """Send a one-shot command to a Hisense device."""

    _attr_has_entity_name = True
    _BUTTONS = {
        "refresh": ("Refresh Device Data", "mdi:refresh", {"t_device_info": "1"}),
        "display_on": ("Display On", "mdi:led-on", {"t_dimmer": "1"}),
        "display_off": ("Display Off", "mdi:led-off", {"t_dimmer": "0"}),
    }

    def __init__(self, coordinator, device, button_type: str) -> None:
        super().__init__(coordinator)
        name, icon, properties = self._BUTTONS[button_type]
        self._device_id = device.puid
        self._properties = properties
        self._button_type = button_type
        self._attr_name = name
        self._attr_icon = icon
        self._attr_unique_id = f"{device.device_id}_{button_type}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.device_id)}, name=device.name,
            manufacturer="Hisense", model=f"{device.type_name} ({device.feature_name})",
        )

    @property
    def _device(self):
        return self.coordinator.get_device(self._device_id)

    @property
    def available(self) -> bool:
        device = self._device
        # A stale 'online' bit with no status is not enough to control a split.
        return bool(device and device.is_online and device.get_status_value(StatusKey.POWER) is not None)

    async def async_press(self) -> None:
        await self.coordinator.async_control_device(
            puid=self._device_id, properties=self._properties
        )
