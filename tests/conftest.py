from __future__ import annotations

"""Test-only stubs for local unit coverage without Home Assistant deps."""

import sys
import types
from enum import IntFlag
from datetime import timezone


def _install_voluptuous_stub() -> None:
    if "voluptuous" in sys.modules:
        return

    module = types.ModuleType("voluptuous")

    class Schema:
        def __init__(self, schema):
            self.schema = schema

        def __call__(self, value):
            return value

    module.Schema = Schema
    module.Required = lambda key, default=None: key
    module.Optional = lambda key, default=None: key
    sys.modules["voluptuous"] = module


def _install_homeassistant_stubs() -> None:
    if "homeassistant" in sys.modules:
        return

    homeassistant = types.ModuleType("homeassistant")
    sys.modules["homeassistant"] = homeassistant

    core = types.ModuleType("homeassistant.core")

    class HomeAssistant:
        pass

    def callback(func):
        return func

    class Event:
        def __init__(self, data=None):
            self.data = data or {}

    core.HomeAssistant = HomeAssistant
    core.callback = callback
    core.Event = Event
    sys.modules["homeassistant.core"] = core

    exceptions = types.ModuleType("homeassistant.exceptions")

    class HomeAssistantError(Exception):
        pass

    exceptions.HomeAssistantError = HomeAssistantError
    sys.modules["homeassistant.exceptions"] = exceptions

    config_entries = types.ModuleType("homeassistant.config_entries")

    class ConfigEntry:
        def __init__(self, entry_id="entry", title="", data=None):
            self.entry_id = entry_id
            self.title = title
            self.data = data or {}

    class OptionsFlow:
        pass

    config_entries.ConfigEntry = ConfigEntry
    config_entries.OptionsFlow = OptionsFlow
    sys.modules["homeassistant.config_entries"] = config_entries

    const = types.ModuleType("homeassistant.const")

    class UnitOfTemperature:
        CELSIUS = "C"
        FAHRENHEIT = "F"

    class Platform:
        CLIMATE = "climate"
        SWITCH = "switch"
        WATER_HEATER = "water_heater"
        NUMBER = "number"
        SENSOR = "sensor"
        HUMIDIFIER = "humidifier"

    const.ATTR_TEMPERATURE = "temperature"
    const.UnitOfTemperature = UnitOfTemperature
    const.Platform = Platform
    const.CONF_NAME = "name"
    const.CONF_CLIENT_ID = "client_id"
    const.CONF_CLIENT_SECRET = "client_secret"
    sys.modules["homeassistant.const"] = const

    data_entry_flow = types.ModuleType("homeassistant.data_entry_flow")
    data_entry_flow.FlowResult = dict
    sys.modules["homeassistant.data_entry_flow"] = data_entry_flow

    helpers = types.ModuleType("homeassistant.helpers")
    sys.modules["homeassistant.helpers"] = helpers

    update_coordinator = types.ModuleType("homeassistant.helpers.update_coordinator")

    class UpdateFailed(Exception):
        pass

    class DataUpdateCoordinator:
        def __init__(self, hass, logger, name=None, update_interval=None):
            self.hass = hass
            self.logger = logger
            self.name = name
            self.update_interval = update_interval
            self.data = None

        async def async_refresh(self):
            if hasattr(self, "_async_update_data"):
                self.data = await self._async_update_data()
            return self.data

        async def async_config_entry_first_refresh(self):
            return await self.async_refresh()

        def async_set_updated_data(self, data):
            self.data = data

    class CoordinatorEntity:
        def __init__(self, coordinator):
            self.coordinator = coordinator
            self.hass = coordinator.hass

        def schedule_update_ha_state(self):
            return None

        async def async_schedule_update_ha_state(self, force_refresh=False):
            return None

        def async_write_ha_state(self):
            return None

        def async_on_remove(self, remove_callback):
            return remove_callback

        async def async_added_to_hass(self):
            return None

    update_coordinator.DataUpdateCoordinator = DataUpdateCoordinator
    update_coordinator.UpdateFailed = UpdateFailed
    update_coordinator.CoordinatorEntity = CoordinatorEntity
    sys.modules["homeassistant.helpers.update_coordinator"] = update_coordinator

    entity = types.ModuleType("homeassistant.helpers.entity")

    class DeviceInfo:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class EntityCategory:
        CONFIG = "config"

    entity.DeviceInfo = DeviceInfo
    entity.EntityCategory = EntityCategory
    sys.modules["homeassistant.helpers.entity"] = entity

    entity_platform = types.ModuleType("homeassistant.helpers.entity_platform")
    entity_platform.AddEntitiesCallback = object
    sys.modules["homeassistant.helpers.entity_platform"] = entity_platform

    event = types.ModuleType("homeassistant.helpers.event")
    event.async_track_state_change_event = lambda hass, entity_ids, cb: (lambda: None)
    sys.modules["homeassistant.helpers.event"] = event

    dispatcher = types.ModuleType("homeassistant.helpers.dispatcher")
    dispatcher.callback = callback
    sys.modules["homeassistant.helpers.dispatcher"] = dispatcher

    config_validation = types.ModuleType("homeassistant.helpers.config_validation")
    config_validation.config_entry_only_config_schema = lambda domain: None
    sys.modules["homeassistant.helpers.config_validation"] = config_validation

    typing_mod = types.ModuleType("homeassistant.helpers.typing")
    typing_mod.ConfigType = dict
    sys.modules["homeassistant.helpers.typing"] = typing_mod

    aiohttp_client = types.ModuleType("homeassistant.helpers.aiohttp_client")
    aiohttp_client.async_get_clientsession = lambda hass: None
    sys.modules["homeassistant.helpers.aiohttp_client"] = aiohttp_client

    oauth2_flow = types.ModuleType("homeassistant.helpers.config_entry_oauth2_flow")

    class AbstractOAuth2FlowHandler:
        def __init_subclass__(cls, **kwargs):
            return None

        @classmethod
        def async_register_implementation(cls, hass, implementation):
            return None

    class LocalOAuth2Implementation:
        def __init__(self, hass=None, domain=None, client_id=None, client_secret=None, authorize_url=None, token_url=None):
            self.hass = hass
            self.domain = domain
            self.client_id = client_id
            self.client_secret = client_secret
            self.authorize_url = authorize_url
            self.token_url = token_url

        async def _token_request(self, data):
            return data

        async def async_generate_authorize_url(self, flow_id):
            return "https://example.invalid"

        async def async_resolve_external_data(self, external_data):
            return external_data

    class OAuth2Session:
        def __init__(self, hass, entry, implementation):
            self.hass = hass
            self.entry = entry
            self.implementation = implementation

        async def async_ensure_token_valid(self):
            return {}

    async def async_get_config_entry_implementation(hass, entry):
        return object()

    oauth2_flow.AbstractOAuth2FlowHandler = AbstractOAuth2FlowHandler
    oauth2_flow.LocalOAuth2Implementation = LocalOAuth2Implementation
    oauth2_flow.OAuth2Session = OAuth2Session
    oauth2_flow.async_get_config_entry_implementation = async_get_config_entry_implementation
    sys.modules["homeassistant.helpers.config_entry_oauth2_flow"] = oauth2_flow

    switch = types.ModuleType("homeassistant.components.switch")

    class SwitchEntity:
        entity_id = "switch.test"

    switch.SwitchEntity = SwitchEntity
    sys.modules["homeassistant.components.switch"] = switch

    water_heater = types.ModuleType("homeassistant.components.water_heater")

    class WaterHeaterEntity:
        pass

    class WaterHeaterEntityFeature(IntFlag):
        TARGET_TEMPERATURE = 1
        OPERATION_MODE = 2
        ON_OFF = 4

    water_heater.WaterHeaterEntity = WaterHeaterEntity
    water_heater.WaterHeaterEntityFeature = WaterHeaterEntityFeature
    water_heater.STATE_HEAT_PUMP = "heat_pump"
    water_heater.STATE_OFF = "off"
    sys.modules["homeassistant.components.water_heater"] = water_heater

    climate = types.ModuleType("homeassistant.components.climate")

    class ClimateEntity:
        pass

    class ClimateEntityFeature(IntFlag):
        TARGET_TEMPERATURE = 1
        FAN_MODE = 2
        SWING_MODE = 4
        TURN_ON = 8
        TURN_OFF = 16

    class HVACMode:
        AUTO = "auto"
        COOL = "cool"
        DRY = "dry"
        FAN_ONLY = "fan_only"
        HEAT = "heat"
        OFF = "off"

    climate.ClimateEntity = ClimateEntity
    climate.ClimateEntityFeature = ClimateEntityFeature
    climate.HVACMode = HVACMode
    sys.modules["homeassistant.components.climate"] = climate

    climate_const = types.ModuleType("homeassistant.components.climate.const")
    climate_const.ATTR_HVAC_MODE = "hvac_mode"
    climate_const.DEFAULT_MAX_TEMP = 30
    climate_const.DEFAULT_MIN_TEMP = 16
    climate_const.SWING_OFF = "off"
    climate_const.SWING_VERTICAL = "vertical"
    climate_const.SWING_HORIZONTAL = "horizontal"
    climate_const.SWING_BOTH = "both"
    sys.modules["homeassistant.components.climate.const"] = climate_const


def _install_pytz_stub() -> None:
    if "pytz" in sys.modules:
        return

    module = types.ModuleType("pytz")
    module.utc = timezone.utc
    sys.modules["pytz"] = module


def _install_aiohttp_stub() -> None:
    if "aiohttp" in sys.modules:
        return

    module = types.ModuleType("aiohttp")

    class ClientError(Exception):
        pass

    class ClientSession:
        def __init__(self, *args, **kwargs):
            self.closed = False

        async def close(self):
            self.closed = True

    module.ClientError = ClientError
    module.ClientSession = ClientSession
    sys.modules["aiohttp"] = module


_install_voluptuous_stub()
_install_homeassistant_stubs()
_install_pytz_stub()
_install_aiohttp_stub()
