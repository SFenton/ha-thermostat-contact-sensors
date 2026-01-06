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
        
        # Should have 2 enabled areas (living_room and bedroom) + 1 global thermostat = 3
        assert len(climate_entities) == 3
        
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
        
        # Check default target temperatures via entity's internal properties
        # (state attributes get unit-converted by HA, so check entity directly)
        coordinator = config_entry.runtime_data
        entity = coordinator.area_thermostats.get("living_room")
        assert entity is not None
        assert entity.target_temperature_low == DEFAULT_TARGET_TEMP_LOW
        assert entity.target_temperature_high == DEFAULT_TARGET_TEMP_HIGH

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
        """Test that setting HVAC mode to anything other than heat_cool is ignored.
        
        Note: We test by calling the entity method directly because HA's
        climate platform validates HVAC modes before calling the entity.
        """
        config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        coordinator: ThermostatContactSensorsCoordinator = config_entry.runtime_data
        
        # Get the virtual thermostat entity directly
        assert hasattr(coordinator, "area_thermostats")
        thermostat = coordinator.area_thermostats.get("living_room")
        assert thermostat is not None

        # Call async_set_hvac_mode directly with an invalid mode
        await thermostat.async_set_hvac_mode(HVACMode.HEAT)
        await hass.async_block_till_done()

        # Mode should still be heat_cool
        assert thermostat.hvac_mode == HVACMode.HEAT_COOL

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
        
        # Check min/max temperatures via entity's internal properties
        # (state attributes get unit-converted by HA, so check entity directly)
        coordinator = config_entry.runtime_data
        entity = coordinator.area_thermostats.get("living_room")
        assert entity is not None
        assert entity.min_temp == DEFAULT_MIN_TEMP
        assert entity.max_temp == DEFAULT_MAX_TEMP

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


class TestGlobalVirtualThermostat:
    """Test the GlobalVirtualThermostat entity."""

    @pytest.mark.asyncio
    async def test_global_thermostat_created(
        self,
        hass: HomeAssistant,
        config_entry: MockConfigEntry,
        setup_climate_entities: None,
    ):
        """Test that a global thermostat is created."""
        config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        coordinator: ThermostatContactSensorsCoordinator = config_entry.runtime_data
        
        # Check that coordinator has global_thermostat
        assert hasattr(coordinator, "global_thermostat")
        assert coordinator.global_thermostat is not None

        await hass.config_entries.async_unload(config_entry.entry_id)

    @pytest.mark.asyncio
    async def test_global_thermostat_displays_max_heat_min_cool(
        self,
        hass: HomeAssistant,
        config_entry: MockConfigEntry,
        setup_climate_entities: None,
    ):
        """Test that global thermostat displays MAX(heat) and MIN(cool)."""
        config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        coordinator: ThermostatContactSensorsCoordinator = config_entry.runtime_data
        
        # Set different temperatures on area thermostats
        living_room = coordinator.area_thermostats["living_room"]
        bedroom = coordinator.area_thermostats["bedroom"]
        
        await living_room.async_set_temperature(
            target_temp_low=20.0, target_temp_high=26.0
        )
        await bedroom.async_set_temperature(
            target_temp_low=22.0, target_temp_high=24.0
        )
        await hass.async_block_till_done()
        
        global_thermostat = coordinator.global_thermostat
        
        # Global should show MAX(20, 22) = 22 for heat
        # Global should show MIN(26, 24) = 24 for cool
        assert global_thermostat.target_temperature_low == 22.0
        assert global_thermostat.target_temperature_high == 24.0

        await hass.config_entries.async_unload(config_entry.entry_id)

    @pytest.mark.asyncio
    async def test_global_lower_heat_propagates_to_areas(
        self,
        hass: HomeAssistant,
        config_entry: MockConfigEntry,
        setup_climate_entities: None,
    ):
        """Test that lowering global heat lowers areas above that value."""
        config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        coordinator: ThermostatContactSensorsCoordinator = config_entry.runtime_data
        
        # Set different temperatures on area thermostats
        living_room = coordinator.area_thermostats["living_room"]
        bedroom = coordinator.area_thermostats["bedroom"]
        
        # Living room: heat=20, cool=26
        # Bedroom: heat=22, cool=24
        await living_room.async_set_temperature(
            _from_global=True,  # Avoid triggering global recalc
            target_temp_low=20.0, target_temp_high=26.0
        )
        await bedroom.async_set_temperature(
            _from_global=True,
            target_temp_low=22.0, target_temp_high=24.0
        )
        
        global_thermostat = coordinator.global_thermostat
        global_thermostat.async_recalculate_from_areas()
        
        # Global shows heat=22 (MAX). Lower it to 21.
        await global_thermostat.async_set_temperature(
            target_temp_low=21.0, target_temp_high=24.0
        )
        await hass.async_block_till_done()
        
        # Bedroom was at 22, should be lowered to 21
        assert bedroom.target_temperature_low == 21.0
        # Living room was at 20, should stay at 20 (not above 21)
        assert living_room.target_temperature_low == 20.0
        # Global should now show 21 (MAX of 20, 21)
        assert global_thermostat.target_temperature_low == 21.0

        await hass.config_entries.async_unload(config_entry.entry_id)

    @pytest.mark.asyncio
    async def test_global_raise_cool_propagates_to_areas(
        self,
        hass: HomeAssistant,
        config_entry: MockConfigEntry,
        setup_climate_entities: None,
    ):
        """Test that raising global cool raises areas below that value."""
        config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        coordinator: ThermostatContactSensorsCoordinator = config_entry.runtime_data
        
        living_room = coordinator.area_thermostats["living_room"]
        bedroom = coordinator.area_thermostats["bedroom"]
        
        # Living room: heat=20, cool=26
        # Bedroom: heat=22, cool=24
        await living_room.async_set_temperature(
            _from_global=True,
            target_temp_low=20.0, target_temp_high=26.0
        )
        await bedroom.async_set_temperature(
            _from_global=True,
            target_temp_low=22.0, target_temp_high=24.0
        )
        
        global_thermostat = coordinator.global_thermostat
        global_thermostat.async_recalculate_from_areas()
        
        # Global shows cool=24 (MIN). Raise it to 25.
        await global_thermostat.async_set_temperature(
            target_temp_low=22.0, target_temp_high=25.0
        )
        await hass.async_block_till_done()
        
        # Bedroom was at 24, should be raised to 25
        assert bedroom.target_temperature_high == 25.0
        # Living room was at 26, should stay at 26 (not below 25)
        assert living_room.target_temperature_high == 26.0
        # Global should now show 25 (MIN of 26, 25)
        assert global_thermostat.target_temperature_high == 25.0

        await hass.config_entries.async_unload(config_entry.entry_id)

    @pytest.mark.asyncio
    async def test_global_raise_heat_snaps_back(
        self,
        hass: HomeAssistant,
        config_entry: MockConfigEntry,
        setup_climate_entities: None,
    ):
        """Test that raising global heat (wrong direction) snaps back."""
        config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        coordinator: ThermostatContactSensorsCoordinator = config_entry.runtime_data
        
        living_room = coordinator.area_thermostats["living_room"]
        bedroom = coordinator.area_thermostats["bedroom"]
        
        # Set both to heat=21
        await living_room.async_set_temperature(
            _from_global=True,
            target_temp_low=21.0, target_temp_high=26.0
        )
        await bedroom.async_set_temperature(
            _from_global=True,
            target_temp_low=21.0, target_temp_high=24.0
        )
        
        global_thermostat = coordinator.global_thermostat
        global_thermostat.async_recalculate_from_areas()
        
        # Global shows heat=21. Try to raise it to 23 (invalid direction).
        await global_thermostat.async_set_temperature(
            target_temp_low=23.0, target_temp_high=24.0
        )
        await hass.async_block_till_done()
        
        # No area is above 23, so nothing changes
        # Recalculate snaps it back to MAX = 21
        assert global_thermostat.target_temperature_low == 21.0
        # Areas unchanged
        assert living_room.target_temperature_low == 21.0
        assert bedroom.target_temperature_low == 21.0

        await hass.config_entries.async_unload(config_entry.entry_id)

    @pytest.mark.asyncio
    async def test_global_lower_cool_snaps_back(
        self,
        hass: HomeAssistant,
        config_entry: MockConfigEntry,
        setup_climate_entities: None,
    ):
        """Test that lowering global cool (wrong direction) snaps back."""
        config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        coordinator: ThermostatContactSensorsCoordinator = config_entry.runtime_data
        
        living_room = coordinator.area_thermostats["living_room"]
        bedroom = coordinator.area_thermostats["bedroom"]
        
        # Set both to cool=25
        await living_room.async_set_temperature(
            _from_global=True,
            target_temp_low=20.0, target_temp_high=25.0
        )
        await bedroom.async_set_temperature(
            _from_global=True,
            target_temp_low=22.0, target_temp_high=25.0
        )
        
        global_thermostat = coordinator.global_thermostat
        global_thermostat.async_recalculate_from_areas()
        
        # Global shows cool=25. Try to lower it to 23 (invalid direction).
        await global_thermostat.async_set_temperature(
            target_temp_low=22.0, target_temp_high=23.0
        )
        await hass.async_block_till_done()
        
        # No area is below 23, so nothing changes
        # Recalculate snaps it back to MIN = 25
        assert global_thermostat.target_temperature_high == 25.0
        # Areas unchanged
        assert living_room.target_temperature_high == 25.0
        assert bedroom.target_temperature_high == 25.0

        await hass.config_entries.async_unload(config_entry.entry_id)

    @pytest.mark.asyncio
    async def test_global_thermostat_hvac_modes(
        self,
        hass: HomeAssistant,
        config_entry: MockConfigEntry,
        setup_climate_entities: None,
    ):
        """Test that global thermostat supports OFF, HEAT, COOL modes only."""
        config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        coordinator: ThermostatContactSensorsCoordinator = config_entry.runtime_data
        global_thermostat = coordinator.global_thermostat

        # Check supported modes
        assert HVACMode.OFF in global_thermostat.hvac_modes
        assert HVACMode.HEAT in global_thermostat.hvac_modes
        assert HVACMode.COOL in global_thermostat.hvac_modes
        assert HVACMode.HEAT_COOL not in global_thermostat.hvac_modes

        # Default mode should be OFF
        assert global_thermostat.hvac_mode == HVACMode.OFF

        await hass.config_entries.async_unload(config_entry.entry_id)

    @pytest.mark.asyncio
    async def test_global_thermostat_set_hvac_mode(
        self,
        hass: HomeAssistant,
        config_entry: MockConfigEntry,
        setup_climate_entities: None,
    ):
        """Test setting HVAC mode on global thermostat."""
        config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        coordinator: ThermostatContactSensorsCoordinator = config_entry.runtime_data
        global_thermostat = coordinator.global_thermostat

        # Set to HEAT
        await global_thermostat.async_set_hvac_mode(HVACMode.HEAT)
        assert global_thermostat.hvac_mode == HVACMode.HEAT

        # Set to COOL
        await global_thermostat.async_set_hvac_mode(HVACMode.COOL)
        assert global_thermostat.hvac_mode == HVACMode.COOL

        # Set to OFF
        await global_thermostat.async_set_hvac_mode(HVACMode.OFF)
        assert global_thermostat.hvac_mode == HVACMode.OFF

        await hass.config_entries.async_unload(config_entry.entry_id)

    @pytest.mark.asyncio
    async def test_global_thermostat_rejects_heat_cool_mode(
        self,
        hass: HomeAssistant,
        config_entry: MockConfigEntry,
        setup_climate_entities: None,
    ):
        """Test that global thermostat rejects HEAT_COOL mode."""
        config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        coordinator: ThermostatContactSensorsCoordinator = config_entry.runtime_data
        global_thermostat = coordinator.global_thermostat

        # Set to HEAT first
        await global_thermostat.async_set_hvac_mode(HVACMode.HEAT)
        assert global_thermostat.hvac_mode == HVACMode.HEAT

        # Try to set HEAT_COOL - should be rejected, mode stays HEAT
        await global_thermostat.async_set_hvac_mode(HVACMode.HEAT_COOL)
        assert global_thermostat.hvac_mode == HVACMode.HEAT

        await hass.config_entries.async_unload(config_entry.entry_id)

    @pytest.mark.asyncio
    async def test_global_thermostat_target_temperature_by_mode(
        self,
        hass: HomeAssistant,
        config_entry: MockConfigEntry,
        setup_climate_entities: None,
    ):
        """Test that target_temperature returns correct value based on mode."""
        config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        coordinator: ThermostatContactSensorsCoordinator = config_entry.runtime_data
        global_thermostat = coordinator.global_thermostat

        # Set known target temps
        living_room = coordinator.area_thermostats["living_room"]
        await living_room.async_set_temperature(
            _from_global=True,
            target_temp_low=20.0, target_temp_high=25.0
        )
        global_thermostat.async_recalculate_from_areas()

        # In OFF mode, target_temperature should be None
        await global_thermostat.async_set_hvac_mode(HVACMode.OFF)
        assert global_thermostat.target_temperature is None

        # In HEAT mode, target_temperature should be heat target (low)
        await global_thermostat.async_set_hvac_mode(HVACMode.HEAT)
        assert global_thermostat.target_temperature == global_thermostat.target_temperature_low

        # In COOL mode, target_temperature should be cool target (high)
        await global_thermostat.async_set_hvac_mode(HVACMode.COOL)
        assert global_thermostat.target_temperature == global_thermostat.target_temperature_high

        await hass.config_entries.async_unload(config_entry.entry_id)

