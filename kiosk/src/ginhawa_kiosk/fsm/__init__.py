"""Session state machine and event bus.

The kiosk session FSM (per Figure 3.7 of the paper) is implemented
with the ``transitions`` library: states like IDLE → RFID_SCAN →
CONSENT → MEASURE_VITALS → MEASURE_ANTHRO → REVIEW → PRINT → IDLE,
with explicit error / abort transitions. The event bus is a thin
synchronous publish/subscribe surface that GUI screens, sensor
adapters, and the audit-writing service all bind to.

The FSM is the SOLE serialiser of BLE operations. CLAUDE.md's "no
two BLE connections at once" rule is enforced here, not at the BLE
adapter level — the FSM holds the lock and owns the lifecycle.
"""
