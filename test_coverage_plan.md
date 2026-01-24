# Test Coverage Plan - Configuration Matrix

## Overview
This document tracks the test coverage for all combinations of Eco Mode, Critical Tracking, Away Behavior, Track Selected Rooms (TSR), and Force Track When Critical (FTCR) settings.

## Configuration Options

### Eco Mode Settings
- **Eco Mode**: On/Off
- **Eco Critical Tracking**: NONE / SELECT / ALL
- **Eco Away Behavior**: DISABLE_ECO / KEEP_ECO_ACTIVE / USE_ECO_AWAY_TARGETS

### Room Tracking Settings
- **TSR (Track Selected Rooms)**: On/Off
- **Tracked Rooms**: None / Some / All
- **FTCR (Force Track When Critical)**: Enabled per room

### Room States
- **Active**: Room occupied past minimum occupancy threshold
- **Inactive**: Room not occupied or below threshold
- **Critical**: Temperature critically hot/cold
- **Satiated**: Temperature at target

---

## Test Coverage Status

### ✅ Completed Tests (Existing)

#### Eco Mode Tests
- [x] **Test 1**: Eco On, SELECT, FTCR Some (inactive)
  - Location: `test_force_track_critical_overrides_eco_mode`
  - Status: ✅ Passing
  
- [x] **Test 5**: Away mode with Disable Eco behavior
  - Location: `test_eco_mode_disabled_when_away_with_disable_eco_behavior`
  - Status: ✅ Passing

#### TSR Tests
- [x] **Test 8**: TSR On, None tracked, FTCR Some (inactive)
  - Location: `test_force_track_critical_overrides_tsr`
  - Status: ✅ Passing
  
- [x] **Test 9**: TSR On, Some tracked, No FTCR (active tracked)
  - Location: `test_tsr_tracked_active_room_gets_normal_evaluation`
  - Status: ✅ Passing

#### Combined Tests
- [x] **Test 13**: Eco On + TSR On, None tracked, FTCR Some (inactive)
  - Location: `test_force_track_critical_with_eco_and_tsr`
  - Status: ✅ Passing
  
- [x] **Test 14**: Eco Off, TSR On, FTCR active room (not tracked)
  - Location: `test_active_room_with_force_track_critical_and_tsr_gets_evaluated`
  - Status: ✅ Passing

#### Critical Temperature Tests
- [x] **Test 18**: Critical inactive room (integration test)
  - Location: `test_critical_cold_room_opens_vents`
  - Status: ✅ Passing
  
- [x] **Test 19**: Critical temperature detection (unit tests)
  - Location: `TestCriticalTemperatureLogic` class
  - Status: ✅ Passing

---

## ❌ Missing Test Coverage

### Priority 1: Eco Critical Tracking Modes

#### ECO_CRITICAL_NONE
- [ ] **Test 2.1**: Eco On, NONE tracking, no rooms critical
  - **Scenario**: All inactive rooms ignored regardless of critical status
  - **Expected**: No inactive rooms tracked, only active rooms evaluated
  - **Room Setup**: 2 inactive (1 critical, 1 normal)

- [ ] **Test 2.2**: Eco On, NONE tracking, with FTCR room
  - **Scenario**: FTCR should still override even with NONE
  - **Expected**: FTCR room still tracked when critical
  - **Room Setup**: 2 inactive with FTCR on 1

#### ECO_CRITICAL_ALL
- [ ] **Test 3.1**: Eco On, ALL tracking, mixed inactive rooms
  - **Scenario**: All inactive rooms tracked for critical temps
  - **Expected**: All inactive rooms evaluated, critical ones flagged
  - **Room Setup**: 3 inactive (1 critical, 2 normal)

- [ ] **Test 3.2**: Eco On, ALL tracking, no critical rooms
  - **Scenario**: No rooms critical
  - **Expected**: All inactive rooms evaluated but none critical
  - **Room Setup**: 2 inactive at normal temps

#### ECO_CRITICAL_SELECT  
- [ ] **Test 4.1**: Eco On, SELECT tracking, no rooms tracked
  - **Scenario**: No rooms in tracked list
  - **Expected**: No inactive rooms evaluated (unless FTCR)
  - **Room Setup**: 2 inactive, 0 in tracked list

- [ ] **Test 4.2**: Eco On, SELECT tracking, all rooms tracked
  - **Scenario**: All rooms in tracked list
  - **Expected**: All inactive tracked rooms evaluated
  - **Room Setup**: 2 inactive, both in tracked list

### Priority 2: Eco Away Behaviors

- [ ] **Test 5.1**: Away with KEEP_ECO_ACTIVE behavior
  - **Scenario**: Away mode, eco should stay on
  - **Expected**: Eco mode remains active, normal eco filtering
  - **Room Setup**: Mix of active/inactive

- [ ] **Test 5.2**: Away with USE_ECO_AWAY_TARGETS behavior
  - **Scenario**: Away mode, use away temp targets
  - **Expected**: Different temp targets used, eco filtering active
  - **Room Setup**: Mix of active/inactive

### Priority 3: TSR Edge Cases

- [ ] **Test 6.1**: TSR On, NO rooms tracked, NO FTCR
  - **Scenario**: TSR enabled but empty tracked list, no overrides
  - **Expected**: No active or inactive rooms evaluated
  - **Room Setup**: 2 active, 2 inactive, none tracked

- [ ] **Test 6.2**: TSR On, ALL rooms tracked
  - **Scenario**: TSR enabled with all rooms in list
  - **Expected**: Behaves same as TSR Off
  - **Room Setup**: 3 active, all in tracked list

- [ ] **Test 6.3**: TSR On, Some tracked, inactive untracked (not critical)
  - **Scenario**: Inactive rooms not tracked and not critical
  - **Expected**: Only tracked inactive rooms evaluated
  - **Room Setup**: 2 inactive (1 tracked normal, 1 untracked normal)

### Priority 4: Complex Combined Scenarios

- [ ] **Test 7.1**: Eco On (ALL) + TSR On + Some tracked
  - **Scenario**: Eco ALL should evaluate all inactive, TSR filters active
  - **Expected**: All inactive evaluated, only tracked active evaluated
  - **Room Setup**: 2 active (1 tracked), 2 inactive

- [ ] **Test 7.2**: Eco On (NONE) + TSR On + FTCR
  - **Scenario**: Eco NONE with TSR and FTCR override
  - **Expected**: No inactive unless FTCR, TSR filters active
  - **Room Setup**: 1 active untracked, 2 inactive (1 with FTCR critical)

- [ ] **Test 7.3**: Eco On (SELECT) + TSR On + Different tracked lists
  - **Scenario**: Eco tracked list differs from TSR tracked list
  - **Expected**: Eco uses its list for inactive, TSR for active
  - **Room Setup**: 2 active, 2 inactive, different tracking

### Priority 5: Room State Combinations

- [ ] **Test 8.1**: Active critical room, FTCR, TSR off, Eco on (SELECT)
  - **Scenario**: Active room gets satiation eval, not critical eval
  - **Expected**: Room evaluated as active (satiation), not critical check
  - **Room Setup**: 1 active critical with FTCR

- [ ] **Test 8.2**: Active non-critical, no FTCR, TSR on (not tracked)
  - **Scenario**: Active but filtered by TSR
  - **Expected**: Room not evaluated at all
  - **Room Setup**: 1 active normal, TSR on, not in list

- [ ] **Test 8.3**: Inactive critical, no FTCR, TSR on (tracked)
  - **Scenario**: TSR doesn't apply to inactive rooms without Eco
  - **Expected**: Room evaluated for critical (no Eco mode)
  - **Room Setup**: 1 inactive critical tracked by TSR

- [ ] **Test 8.4**: Multiple FTCR rooms, different states
  - **Scenario**: Mix of FTCR rooms in different states
  - **Expected**: All FTCR rooms evaluated appropriately
  - **Room Setup**: 3 rooms with FTCR (1 active, 1 inactive critical, 1 inactive normal)

---

## Test Implementation Checklist

### Phase 1: Core Eco Mode Variants (Priority 1)
- [ ] Create test helper for eco mode setup
- [ ] Test ECO_CRITICAL_NONE (2 tests)
- [ ] Test ECO_CRITICAL_ALL (2 tests)
- [ ] Test ECO_CRITICAL_SELECT edge cases (2 tests)

### Phase 2: Away Behaviors (Priority 2)
- [ ] Create test helper for away mode setup
- [ ] Test KEEP_ECO_ACTIVE behavior
- [ ] Test USE_ECO_AWAY_TARGETS behavior

### Phase 3: TSR Edge Cases (Priority 3)
- [ ] Test empty tracked list
- [ ] Test all rooms tracked
- [ ] Test partial tracking without FTCR

### Phase 4: Complex Scenarios (Priority 4)
- [ ] Eco ALL + TSR combinations
- [ ] Eco NONE + TSR + FTCR
- [ ] Eco SELECT + TSR with different lists

### Phase 5: State Combinations (Priority 5)
- [ ] Active critical with various settings
- [ ] TSR filtering of active rooms
- [ ] Multiple FTCR rooms
- [ ] Edge cases

---

## Notes

### Test Data Patterns
- Use consistent room names: office, bedroom, living_room, music_room, kitchen
- Use consistent sensor naming: `sensor.{room}_temp`, `binary_sensor.{room}_motion`
- Critical threshold: 3°C below/above target by default
- Default target: 22°C heat, 24°C cool

### Assertions to Include
1. Room presence in `room_states` dict
2. `is_critical` flag value
3. `is_satiated` flag value  
4. `is_active` flag value
5. `determining_temperature` value
6. Vent states if applicable

### Common Setup Code Needed
- Helper function: `create_room_config(room_id, tracked=False, ftcr=False)`
- Helper function: `setup_eco_coordinator(eco_mode, eco_critical_tracking, eco_away_behavior)`
- Helper function: `setup_tsr_coordinator(tracked_rooms)`
- Fixture: `mixed_room_states` (active, inactive, critical, normal)

---

## Progress Tracking

**Total Tests Identified**: 26
- **Existing**: 8 ✅
- **Missing**: 18 ❌
- **Completion**: 31%

**By Priority**:
- Priority 1 (Eco Modes): 0/6 complete
- Priority 2 (Away): 0/2 complete
- Priority 3 (TSR): 0/3 complete
- Priority 4 (Complex): 0/3 complete
- Priority 5 (States): 0/4 complete

---

## Last Updated
January 24, 2026
