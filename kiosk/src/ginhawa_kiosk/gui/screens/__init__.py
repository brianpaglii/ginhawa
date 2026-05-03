"""One QWidget per FSM state.

Each screen is small and presentational: it renders the strings for
the active language, owns its own user-input widgets, and emits
**user-action signals** (e.g., ``LanguageSelectScreen.language_chosen``)
when the citizen taps a button. The main window subscribes to those
signals and forwards them to the FSM as triggers; the FSM then emits
``state_changed`` and the main window switches the visible page.

Screens MUST NOT:

* Call ``main_window.set_state()`` directly — that would bypass the
  FSM's transition validation.
* Hold long-lived references to Citizen / Session / Measurement
  ORM objects across language changes — those references can become
  stale when the citizen taps "Change language" and the screen is
  re-entered. Pull what you need from the :class:`FsmSnapshot` passed
  into ``on_enter``.
"""

from .aborted import AbortedScreen
from .base import BaseScreen
from .consent import ConsentScreen
from .end import EndScreen
from .error import ErrorScreen
from .identifying import IdentifyingScreen
from .idle import IdleScreen
from .language_select import LanguageSelectScreen
from .measuring_anthro import MeasuringAnthroScreen
from .measuring_vitals import MeasuringVitalsScreen
from .path_choice import PathChoiceScreen
from .printing import PrintingScreen
from .register_form import RegisterFormScreen, RegistrationData
from .report import ReportRow, ReportScreen

__all__ = [
    "AbortedScreen",
    "BaseScreen",
    "ConsentScreen",
    "EndScreen",
    "ErrorScreen",
    "IdentifyingScreen",
    "IdleScreen",
    "LanguageSelectScreen",
    "MeasuringAnthroScreen",
    "MeasuringVitalsScreen",
    "PathChoiceScreen",
    "PrintingScreen",
    "RegisterFormScreen",
    "RegistrationData",
    "ReportRow",
    "ReportScreen",
]
