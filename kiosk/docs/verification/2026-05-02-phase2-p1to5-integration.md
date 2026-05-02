# Phase 2 (Prompts 1–5) Integration Test

**Date:** 2026-05-02
**Test file:** [`kiosk/tests/integration/test_phase2_p1to5_integration.py`](../../tests/integration/test_phase2_p1to5_integration.py)
**Verdict:** **PASS (2/2)**

## Scope

Verifies the Phase 2 layers compose correctly end-to-end. This is
not a re-test of behaviour the unit suites already cover — those
guarantees are assumed and not re-asserted here. The integration
test asserts only the _seams_ between layers:

- Schema → models → SQLCipher engine → `init_database`
- Event bus → FSM triggers → Session row mutations
- Validation service → Measurement row's `is_valid` /
  `validation_notes`
- `record_audit` writing rows from both the FSM and the wiring
  driver
- Sync daemon → `CloudClient` → mocked cloud → local `synced=1`
  in FK-safe order

Hardware is not required: the cloud is a pytest-httpx mock, sensors
are simulated by publishing `MeasurementProposed` events, and the
SQLCipher database lives under `tmp_path`.

## Setup

| Step      | What                                                                                                                                                                             | Source                                     |
| --------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------ |
| Database  | Fresh SQLCipher file under `tmp_path`; tables created via `init_database` (which uses `Base.metadata.create_all`, the canonical kiosk init path against `/schema.sql`).          | `conftest`-style fixtures in the test      |
| Citizen   | One row inserted via the model layer: `id=00000000-…-0101`, `rfid_uid='INTEGRATION_TEST_001'`, `barangay='Tibagan'`, `consent_version='1.0'`, `is_active=1`.                     | `citizen` fixture                          |
| FSM + Bus | Real `SessionFSM` + real `EventBus` instances; no mocking.                                                                                                                       | `fsm`, `bus` fixtures                      |
| Wiring    | `IntegrationDriver` subscribes to bus events and translates them into FSM triggers + DB mutations. Mirrors the production GUI/sensor-adapter wiring that lands in later prompts. | `IntegrationDriver` class in the test file |
| Cloud     | All three sync endpoints mocked by `pytest-httpx` to return `created` for every record.                                                                                          | `httpx_mock` fixture                       |

## Test 1 — `test_full_session_with_sync_daemon`

### Driven thread

| #   | Event published                                           | Expected FSM state                           |
| --- | --------------------------------------------------------- | -------------------------------------------- |
| 1   | `RfidScanned(uid='INTEGRATION_TEST_001')`                 | `MENU` (after lookup → `citizen_identified`) |
| 2   | `PathSelected(path='full')`                               | `MEASURING_VITALS`                           |
| 3   | `MeasurementProposed(systolic_bp=128, mmHg, mock_omron)`  | `MEASURING_VITALS` (no transition)           |
| 4   | `MeasurementProposed(diastolic_bp=82, mmHg, mock_omron)`  | `MEASURING_VITALS`                           |
| 5   | `MeasurementProposed(spo2=98, %, mock_max30100)`          | `MEASURING_VITALS`                           |
| 6   | `MeasurementProposed(heart_rate=72, bpm, mock_max30100)`  | `MEASURING_VITALS`                           |
| 7   | `MeasurementProposed(temperature=36.5, C, mock_mlx90640)` | `MEASURING_VITALS`                           |
| 8   | `MeasurementPathCompleteEvent()`                          | `MEASURING_ANTHROPOMETRIC`                   |
| 9   | `MeasurementProposed(height=165, cm, mock_vl53l0x)`       | `MEASURING_ANTHROPOMETRIC`                   |
| 10  | `MeasurementProposed(weight=65, kg, mock_xiaomi)`         | `MEASURING_ANTHROPOMETRIC`                   |
| 11  | `MeasurementProposed(bmi=23.9, '', derived)`              | `MEASURING_ANTHROPOMETRIC`                   |
| 12  | `MeasurementPathCompleteEvent()`                          | `REPORT`                                     |
| 13  | `FinishWithoutPrintingEvent()`                            | `END`                                        |
| 14  | `AcknowledgeEvent()`                                      | `IDLE`                                       |

### Database assertions (PASS)

| Assertion                                                         | Result                             |
| ----------------------------------------------------------------- | ---------------------------------- |
| Exactly 1 session row exists for the citizen                      | ✅ 1                               |
| `status='completed'`                                              | ✅                                 |
| `ended_at` non-null                                               | ✅                                 |
| `printed_status='not_requested'`                                  | ✅                                 |
| `measurement_path='full'`                                         | ✅                                 |
| Exactly 8 measurement rows for that session                       | ✅ 8 (5 vitals + 3 anthropometric) |
| Each measurement has the correct `(type, value, unit)`            | ✅                                 |
| Every measurement has `is_valid=1` (all in range, expected units) | ✅                                 |
| Every measurement has `synced=0` (daemon hasn't run yet)          | ✅                                 |

### Audit assertions (PASS)

| Action                                                              | Required actor_type pattern          | Result                                                                                                          |
| ------------------------------------------------------------------- | ------------------------------------ | --------------------------------------------------------------------------------------------------------------- |
| `citizen.read`                                                      | `kiosk` (driver-issued lookup audit) | ✅                                                                                                              |
| `fsm.rfid_scanned`                                                  | `citizen`                            | ✅                                                                                                              |
| `fsm.menu`                                                          | `citizen`                            | ✅                                                                                                              |
| `fsm.path_selected`                                                 | `citizen`                            | ✅                                                                                                              |
| `fsm.measurement_captured` × 8                                      | `citizen`                            | ✅ count == 8                                                                                                   |
| `fsm.measurement_path_step` (vitals → anthropometric leg of `full`) | `citizen`                            | ✅                                                                                                              |
| `fsm.report`                                                        | `citizen`                            | ✅                                                                                                              |
| `fsm.finish_without_printing`                                       | `citizen`                            | ✅                                                                                                              |
| `fsm.acknowledge`                                                   | `system`                             | ✅                                                                                                              |
| Mix of `citizen` and `system` actor_types observed                  | —                                    | ✅                                                                                                              |
| No `admin` actor_type appears                                       | —                                    | ✅ (kiosk has no admin principal — ADR not yet numbered, captured in `services/audit.py`'s `ActorType` literal) |

### Sync daemon assertions (PASS)

| Assertion                                | Result                                  |
| ---------------------------------------- | --------------------------------------- |
| Daemon called endpoints in FK-safe order | ✅ `[citizens, sessions, measurements]` |
| `Citizen.synced` flipped to 1            | ✅                                      |
| `Session.synced` flipped to 1            | ✅                                      |
| All 8 `Measurement.synced` flipped to 1  | ✅                                      |

## Test 2 — `test_out_of_range_measurement_marked_invalid`

### Setup

Same citizen, same fresh DB. Drive a fresh session up to
`MEASURING_VITALS` and publish a single `MeasurementProposed` with
`systolic_bp=300, unit=mmHg, is_valid=True` (the kiosk's _belief_).

### Assertions (PASS)

| Assertion                                                                                                | Result |
| -------------------------------------------------------------------------------------------------------- | ------ |
| Row stored despite out-of-range value                                                                    | ✅     |
| `is_valid=0` (validation service overrode the kiosk's belief)                                            | ✅     |
| `validation_notes` contains `outside physiological range`                                                | ✅     |
| `validation_notes` contains the offending value `300.0`                                                  | ✅     |
| Driver's recorded `last_validation_notes` matches the persisted note (proves the validation service ran) | ✅     |
| `synced=0` (daemon not run)                                                                              | ✅     |
| Session has exactly 1 measurement (no extra rows)                                                        | ✅     |

## What this test is and is not

**Is**: a thin seam-tester. Every assertion targets the boundary
between two layers — never the internal behaviour of one layer in
isolation.

**Is not**: a substitute for the unit suites. The behaviours covered
elsewhere are assumed to hold:

| Layer                                | Where its behaviour is exhaustively tested       |
| ------------------------------------ | ------------------------------------------------ |
| `db/session.py` SQLCipher engagement | `tests/db/test_session.py`                       |
| Per-table model round-trips          | `tests/db/test_models.py`                        |
| FSM per-transition logic             | `tests/fsm/test_session_fsm.py` + `_branches.py` |
| Event bus pub/sub semantics          | `tests/fsm/test_event_bus.py`                    |
| Range / unit validation              | `tests/services/test_validation.py`              |
| Audit writer JSON / IP behaviour     | `tests/services/test_audit.py`                   |
| Cloud client failure modes           | `tests/sync/test_client.py`                      |
| Sync daemon per-record outcomes      | `tests/sync/test_daemon.py`                      |

If any of those suites regresses, this test will likely catch it
indirectly — but the failure message will point at the seam, not at
the offending unit. Look there first when this test is the only one
that breaks.

## Findings

None. Both tests pass cleanly on first run. mypy --strict clean
across 27 kiosk source files; ruff format clean.

### Follow-up applied (post-review)

The first-pass version of this test defined `MeasurementProposed`
locally inside `tests/integration/`. On review the event was
promoted into the production bus at
`kiosk/src/ginhawa_kiosk/fsm/event_bus.py` (replacing the unused
`MeasurementCaptured(measurement_id)` event — no production code
subscribed to it). The integration test now imports the production
event class. Field shape:

```python
class MeasurementProposed(Event):
    measurement_type: str
    value: float
    unit: str
    source_device: str
    claimed_is_valid: bool   # kiosk's belief; validation may override
```

Single-write semantics: the wiring driver runs
`validate_measurement` once and persists one `Measurement` row with
the validation-service's verdict — never two rows, never an in-place
update of an earlier "proposed" row.

## How to re-run

```bash
cd kiosk
uv run pytest tests/integration/ -v
```

Full kiosk suite (66 tests):

```bash
cd kiosk
uv run pytest -q
```
