"""PyQt6 GUI package for the kiosk.

The kiosk runs as a single PyQt6 application: ``KioskMainWindow``
(in :mod:`.main_window`) owns a ``QStackedWidget`` whose pages map
1:1 to the FSM's states. State transitions on the FSM emit a Qt
signal that the main window listens to, switching the visible page;
the screens themselves emit user-action signals (e.g.,
``language_chosen``, ``consent_given``) that the main window
forwards to the FSM as triggers. Screens never directly call
``main_window.set_state()`` — the FSM is the source of truth.

Subpackages:

* :mod:`.screens` — one widget per FSM state.
* :mod:`.strings` — bilingual string catalogue (EN/TL).

Importing this package does NOT instantiate any Qt widgets. Widget
construction happens only when :class:`.main_window.KioskMainWindow`
is instantiated in :mod:`ginhawa_kiosk.__main__`.
"""
