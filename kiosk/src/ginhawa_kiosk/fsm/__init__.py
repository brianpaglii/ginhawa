"""Session state machine and event bus.

Per ADR / paper Figure 3.8: the kiosk session FSM models IDLE → RFID
scan → identify or register → consent → menu → measurements → report
→ end, plus the explicit error/abort branches.

Implementation uses the ``transitions`` library for the state graph
and a small async pub/sub bus for routing typed events from sensors
and the GUI to the FSM. The FSM is the SOLE serialiser of BLE
operations (CLAUDE.md, "no concurrent BLE"); it holds the lock and
owns each device's lifecycle.
"""

from .event_bus import (
    Acknowledge,
    BpMeasurementRequestCancelled,
    BpMeasurementRequested,
    CitizenIdentified,
    ConsentGiven,
    ConsentRefused,
    ErrorOccurred,
    Event,
    EventBus,
    FinishWithoutPrinting,
    MeasurementProposed,
    MeasurementPathComplete,
    PaperOutDetected,
    PathSelected,
    PrintComplete,
    PrintRequested,
    RfidScanned,
    SessionResetForSensors,
    TimeoutFired,
)
from .session_fsm import (
    FsmSnapshot,
    IdentificationResult,
    Language,
    SessionFSM,
    State,
)

__all__ = [
    "Acknowledge",
    "BpMeasurementRequestCancelled",
    "BpMeasurementRequested",
    "CitizenIdentified",
    "ConsentGiven",
    "ConsentRefused",
    "ErrorOccurred",
    "Event",
    "EventBus",
    "FinishWithoutPrinting",
    "FsmSnapshot",
    "IdentificationResult",
    "Language",
    "MeasurementProposed",
    "MeasurementPathComplete",
    "PaperOutDetected",
    "PathSelected",
    "PrintComplete",
    "PrintRequested",
    "RfidScanned",
    "SessionFSM",
    "SessionResetForSensors",
    "State",
    "TimeoutFired",
]
