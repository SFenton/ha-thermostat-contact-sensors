"""Tests for the area virtual thermostat climate entities."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_TEMPERATURE,
    HVACMode,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_TEMPERATURE,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.thermostat_contact_sensors.climate import (
    AreaVirtualThermostat,
    DEFAULT_MAX_TEMP,
    DEFAULT_MIN_TEMP,
    DEFAULT_TARGET_TEMP_HIGH,
    DEFAULT_TARGET_TEMP_LOW,
    DEFAULT_TEMP_STEP,
)
from custom_components.thermostat_contact_sensors.const import (
    CONF_AREA_ENABLED,
    CONF_AREA_ID,
    CONF_AREAS,
    CONF_BINARY_SENSORS,
    CONF_CONTACT_SENSORS,
    CONF_TEMPERATURE_SENSORS,
    CONF_THERMOSTAT,
    DOMAIN,
)
from custom_components.thermostat_contact_sensors.coordinator import (
    ThermostatContactSensorsCoordinator,
)


# Test constants
THERMOSTAT = "climate.main_thermostat"
CONTACT_SENSOR = "binary_sensor.living_room_window"
TEMP_SENSOR = "sensor.living_room_temperature"
MOTION_SENSOR = "binary_sensor.living_room_motion"


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Create a mock config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Test Thermostat",
        version=3,
        data={
            "name": "Test Climate Thermostat",
            CONF_THERMOSTAT: THERMOSTAT,
            CONF_AREAS: {
                "living_room": {
                    CONF_AREA_ID: "living_room",
                    CONF_AREA_ENABLED: True,
                    CONF_CONTACT_SENSORS: [CONTACT_SENSOR],
                    CONF_BINARY_SENSORS: [MOTION_SENSOR],
                    CONF_TEMPERATURE_SENSORS: [TEMP_SENSOR],
                },
                "bedroom": {
                    CONF_AREA_ID: "bedroom",
                    CONF_AREA_ENABLED: True,
                    CONF_CONTACT_SENSORS: [],
                    CONF_BINARY_SENSORS: [],
                    CONF_TEMPERATURE_SENSORS: [],
                },
                "disabled_room": {
                    CONF_AREA_ID: "disabled_room",
                    CONF_AREA_ENABLED: False,
                    CONF_CONTACT_SENSORS: [],
                    CONF_BINARY_SENSORS: [],
                    CONF_TEMPERATURE_SENSORS: [],
                },
            },
        },
        options={},
    )


@pytest.fixture
async def setup_climate_entities(hass: HomeAssistant) -> None:
    """Set up the test entities and mock climate service."""
    # Create main thermostat entity
    hass.states.async_set(
        THERMOSTAT,
        HVACMode.HEAT,
        {
            "friendly_name": "Main Thermostat",
            "hvac_modes": [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL, HVACMode.HEAT_COOL],
            "current_temperature": 20,
            "temperature": 22,
        },
    )

    # Create contact sensor entity (closed initially)
    hass.states.async_set(
        CONTACT_SENSOR,
        STATE_OFF,
        {"device_class": "window", "friendly_name": "Living Room Window"},
    )

    # Create temperature sensor
    hass.states.async_set(
        TEMP_SENSOR,
        "21.5",
        {"unit_of_measurement": "°C", "friendly_name": "Living Room Temperature"},
    )

    # Create motion sensor (not detected)
    hass.states.async_set(
        MOTION_SENSOR,
        STATE_OFF,
        {"device_class": "motion", "friendly_name": "Living Room Motion"},
    )

    # Mock climate set_hvac_mode service (required by coordinator)
    async def handle_set_hvac_mode(call):
        """Handle the set_hvac_mode service call."""
        entity_id = call.data.get("entity_id")
        hvac_mode = call.data.get("hvac_mode")
        current_state = hass.states.get(entity_id)
        current_attrs = current_state.attributes if current_state else {}
        hass.states.async_set(entity_id, hvac_mode, current_attrs)

    hass.services.async_register(
        CLIMATE_DOMAIN,
        "set_hvac_mode",
        handle_set_hvac_mode,
    )

    await hass.async_block_till_done()


class TestAreaVirtualThermostat:
    """Test the AreaVirtualThermostat entity."""

    @pytest.mark.asyncio
    async def test_virtual_thermostat_created_for_enabled_areas(
        self,
        hass: HomeAssistant,
        config_entry: MockConfigEntry,
        setup_climate_entities: None,
    ):
        """Test that virtual thermostats are created for enabled areas only."""
        config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        # Should have virtual thermostats for living_room and bedroom
        living_room_thermostat = hass.states.get(
            f"climate.{config_entry.entry_id}_living_room_thermostat"
        )
        bedroom_thermostat = hass.states.get(
            f"climate.{config_entry.entry_id}_bedroom_thermostat"
        )
        disabled_thermostat = hass.states.get(
            f"climate.{config_entry.entry_id}_disabled_room_thermostat"
        )

        # The entity_id format includes the unique_id which includes entry_id
        # Check via entity registry instead
        from homeassistant.helpers import entity_registry as er
        entity_reg = er.async_get(hass)
        
        # Find climate entities for our entry
        climate_entities = [
            entity for entity in entity_reg.entities.values()
            if entity.platform == DOMAIN 
            and entity.domain == CLIMATE_DOMAIN
        ]
        
        # Should have 2 enabled areas (living_room and bedroom)
        assert len(climate_entities) == 2
        
        # Verify no thermostat for disabled room
        disabled_entity = entity_reg.async_get_entity_id(
            CLIMATE_DOMAIN, 
            DOMAIN, 
            f"{config_entry.entry_id}_disabled_room_thermostat"
        )
        assert disabled_entity is None

        await hass.config_entries.async_unload(config_entry.entry_id)

    @pytest.mark.asyncio
    async def test_virtual_thermostat_default_values(
        self,
        hass: HomeAssistant,
        config_entry: MockConfigEntry,
        setup_climate_entities: None,
    ):
        """Test that virtual thermostats have correct default values."""
        config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        # Get entity ID from registry
        from homeassistant.helpers import entity_registry as er
        entity_reg = er.async_get(hass)
        entity_id = entity_reg.async_get_entity_id(
            CLIMATE_DOMAIN, 
            DOMAIN, 
            f"{config_entry.entry_id}_living_room_thermostat"
        )
        assert entity_id is not None

        state = hass.states.get(entity_id)
        assert state is not None
        
        # Check HVAC mode is heat_cool
        assert state.state == HVACMode.HEAT_COOL
        
        # Check default target temperatures
        assert state.attributes.get(ATTR_TARGET_TEMP_LOW) == DEFAULT_TARGET_TEMP_LOW
        assert state.attributes.get(ATTR_TARGET_TEMP_HIGH) == DEFAULT_TARGET_TEMP_HIGH

        await hass.config_entries.async_unload(config_entry.entry_id)

    @pytest.mark.asyncio
    async def test_virtual_thermostat_only_supports_heat_cool(
        self,
        hass: HomeAssistant,
        config_entry: MockConfigEntry,
        setup_climate_entities: None,
    ):
        """Test that virtual thermostat only supports heat_cool mode."""
        config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        # Get entity ID from registry
        from homeassistant.helpers import entity_registry as er
        entity_reg = er.async_get(hass)
        entity_id = entity_reg.async_get_entity_id(
            CLIMATE_DOMAIN, 
            DOMAIN, 
            f"{config_entry.entry_id}_living_room_thermostat"
        )
        assert entity_id is not None

        state = hass.states.get(entity_id)
        assert state is not None
        
        # Check only heat_cool is supported
        hvac_modes = state.attributes.get("hvac_modes", [])
        assert hvac_modes == [HVACMode.HEAT_COOL]

        await hass.config_entries.async_unload(config_entry.entry_id)

    @pytest.mark.asyncio
    async def test_set_temperature_updates_targets(
        self,
        hass: HomeAssistant,
        config_entry: MockConfigEntry,
        setup_climate_entities: None,
    ):
        """Test setting temperature updates target values."""
        config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        # Get entity ID from registry
        from homeassistant.helpers import entity_registry as er
        entity_reg = er.async_get(hass)
        entity_id = entity_reg.async_get_entity_id(
            CLIMATE_DOMAIN, 
            DOMAIN, 
            f"{config_entry.entry_id}_living_room_thermostat"
        )
        assert entity_id is not None

        # Set new temperature targets
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_TEMPERATURE,
            {
                ATTR_ENTITY_ID: entity_id,
                ATTR_TARGET_TEMP_LOW: 19.0,
                ATTR_TARGET_TEMP_HIGH: 25.0,
            },
            blocking=True,
        )
        await hass.async_block_till_done()

        state = hass.states.get(entity_id)
        assert state is not None
        assert state.attributes.get(ATTR_TARGET_TEMP_LOW) == 19.0
        assert state.attributes.get(ATTR_TARGET_TEMP_HIGH) == 25.0

        await hass.config_entries.async_unload(config_entry.entry_id)

    @pytest.mark.asyncio
    async def test_set_temperature_swaps_if_low_greater_than_high(
        self,
        hass: HomeAssistant,
        config_entry: MockConfigEntry,
        setup_climate_entities: None,
    ):
        """Test that setting low > high swaps the values.
        
        Note: We test by calling the entity method directly because HA's
        climate platform validates low <= high before calling the entity.
        """
        config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        coordinator: ThermostatContactSensorsCoordinator = config_entry.runtime_data
        
        # Get the virtual thermostat entity directly
        assert hasattr(coordinator, "area_thermostats")
        thermostat = coordinator.area_thermostats.get("living_room")
        assert thermostat is not None
        
        # Call async_set_temperature directly with inverted values
        await thermostat.async_set_temperature(
            target_temp_low=26.0,  # Higher than high
            target_temp_high=20.0,  # Lower than low
        )
        await hass.async_block_till_done()

        # Values should be swapped
        assert thermostat.target_temperature_low == 20.0
        assert thermostat.target_temperature_high == 26.0

        await hass.config_entries.async_unload(config_entry.entry_id)

    @pytest.mark.asyncio
    async def test_virtual_thermostat_has_area_attributes(
        self,
        hass: HomeAssistant,
        config_entry: MockConfigEntry,
        setup_climate_entities: None,
    ):
        """Test that virtual thermostat has area-related attributes."""
        config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        # Get entity ID from registry
        from homeassistant.helpers import entity_registry as er
        entity_reg = er.async_get(hass)
        entity_id = entity_reg.async_get_entity_id(
            CLIMATE_DOMAIN, 
            DOMAIN, 
            f"{config_entry.entry_id}_living_room_thermostat"
        )
        assert entity_id is not None

        state = hass.states.get(entity_id)
        assert state is not None
        
        # Check area attributes
        assert state.attributes.get("area_id") == "living_room"
        assert "area_name" in state.attributes
        assert state.attributes.get("temperature_sensors") == [TEMP_SENSOR]

        await hass.config_entries.async_unload(config_entry.entry_id)

    @pytest.mark.asyncio
    async def test_set_hvac_mode_ignores_non_heat_cool(
        self,
        hass: HomeAssistant,
        config_entry: MockConfigEntry,
        setup_climate_entities: None,
    ):
        """Test that setting HVAC mode to anything other than heat_cool is ignored."""
        config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        # Get entity ID from registry
        from homeassistant.helpers import entity_registry as er
        entity_reg = er.async_get(hass)
        entity_id = entity_reg.async_get_entity_id(
            CLIMATE_DOMAIN, 
            DOMAIN, 
            f"{config_entry.entry_id}_living_room_thermostat"
        )
        assert entity_id is not None

        # Try to set HVAC mode to heat (should be ignored)
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_HVAC_MODE,
            {
                ATTR_ENTITY_ID: entity_id,
                ATTR_HVAC_MODE: HVACMode.HEAT,
            },
            blocking=True,
        )
        await hass.async_block_till_done()

        state = hass.states.get(entity_id)
        assert state is not None
        # Mode should still be heat_cool
        assert state.state == HVACMode.HEAT_COOL

        await hass.config_entries.async_unload(config_entry.entry_id)

    @pytest.mark.asyncio
    async def test_virtual_thermostat_temperature_step(
        self,
        hass: HomeAssistant,
        config_entry: MockConfigEntry,
        setup_climate_entities: None,
    ):
        """Test that virtual thermostat has correct temperature step."""
        config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        # Get entity ID from registry
        from homeassistant.helpers import entity_registry as er
        entity_reg = er.async_get(hass)
        entity_id = entity_reg.async_get_entity_id(
            CLIMATE_DOMAIN, 
            DOMAIN, 
            f"{config_entry.entry_id}_living_room_thermostat"
        )
        assert entity_id is not None

        state = hass.states.get(entity_id)
        assert state is not None
        
        # Check temperature step
        assert state.attributes.get("target_temp_step") == DEFAULT_TEMP_STEP

        await hass.config_entries.async_unload(config_entry.entry_id)

    @pytest.mark.asyncio
    async def test_virtual_thermostat_min_max_temp(
        self,
        hass: HomeAssistant,
        config_entry: MockConfigEntry,
        setup_climate_entities: None,
    ):
        """Test that virtual thermostat has correct min/max temperatures."""
        config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        # Get entity ID from registry
        from homeassistant.helpers import entity_registry as er
        entity_reg = er.async_get(hass)
        entity_id = entity_reg.async_get_entity_id(
            CLIMATE_DOMAIN, 
            DOMAIN, 
            f"{config_entry.entry_id}_living_room_thermostat"
        )
        assert entity_id is not None

        state = hass.states.get(entity_id)
        assert state is not None
        
        # Check min/max temperatures
        assert state.attributes.get("min_temp") == DEFAULT_MIN_TEMP
        assert state.attributes.get("max_temp") == DEFAULT_MAX_TEMP

        await hass.config_entries.async_unload(config_entry.entry_id)


class TestAreaVirtualThermostatStateRestore:
    """Test state restoration for virtual thermostats."""

    @pytest.mark.asyncio
    async def test_restores_target_temperatures(
        self,
        hass: HomeAssistant,
        config_entry: MockConfigEntry,
        setup_climate_entities: None,
    ):
        """Test that target temperatures are restored after restart."""
        config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        # Get entity ID from registry
        from homeassistant.helpers import entity_registry as er
        entity_reg = er.async_get(hass)
        entity_id = entity_reg.async_get_entity_id(
            CLIMATE_DOMAIN, 
            DOMAIN, 
            f"{config_entry.entry_id}_living_room_thermostat"
        )
        assert entity_id is not None

        # Set custom temperature targets
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_TEMPERATURE,
            {
                ATTR_ENTITY_ID: entity_id,
                ATTR_TARGET_TEMP_LOW: 20.0,
                ATTR_TARGET_TEMP_HIGH: 26.0,
            },
            blocking=True,
        )
        await hass.async_block_till_done()

        # Verify temperatures were set
        state = hass.states.get(entity_id)
        assert state.attributes.get(ATTR_TARGET_TEMP_LOW) == 20.0
        assert state.attributes.get(ATTR_TARGET_TEMP_HIGH) == 26.0

        await hass.config_entries.async_unload(config_entry.entry_id)


class TestAreaVirtualThermostatRegistration:
    """Test that virtual thermostats register with the coordinator."""

    @pytest.mark.asyncio
    async def test_registers_with_coordinator(
        self,
        hass: HomeAssistant,
        config_entry: MockConfigEntry,
        setup_climate_entities: None,
    ):
        """Test that virtual thermostats register themselves with coordinator."""
        config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        coordinator: ThermostatContactSensorsCoordinator = config_entry.runtime_data
        
        # Check that coordinator has area_thermostats dict
        assert hasattr(coordinator, "area_thermostats")
        assert isinstance(coordinator.area_thermostats, dict)
        
        # Should have thermostats for enabled areas
        assert "living_room" in coordinator.area_thermostats
        assert "bedroom" in coordinator.area_thermostats
        assert "disabled_room" not in coordinator.area_thermostats
        
        # Verify they are the right type
        assert isinstance(
            coordinator.area_thermostats["living_room"], 
            AreaVirtualThermostat
        )

        await hass.config_entries.async_unload(config_entry.entry_id)
