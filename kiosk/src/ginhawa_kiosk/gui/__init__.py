"""PyQt6 screens.

Phase 2 scaffolding only — placeholders for IDLE / RFID / CONSENT /
MEASURE / REVIEW / PRINT screens land here in subsequent prompts.
The GUI imports from ``fsm/`` (state transitions) and ``services/``
(measurement / printer outcomes); it does NOT import from ``db/`` or
``sensors/`` directly. The FSM is the single rendezvous point.

PyQt6 imports are deliberately not pulled into this ``__init__`` so
the package can be imported on a headless CI runner without the Qt6
system libraries installed.
"""
