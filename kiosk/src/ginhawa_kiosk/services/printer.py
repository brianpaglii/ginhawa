"""Thermal printer service (Xprinter XP-58IIH ESC/POS).

Concrete python-escpos wiring lands in a later prompt; this scaffold
captures the printer-related contracts so the FSM can already encode
its printed_status outcomes.

CLAUDE.md absolute rules:
* The printer is NEVER powered from the Pi's USB rail or 5 V GPIO.
  It uses its own 9 V external adapter. Sharing power causes
  brownouts during high-density print lines.
* A print failure is best-effort — the session record is saved
  regardless of whether the receipt prints. ``PrintedStatus`` carries
  one of ``not_requested`` / ``printed_ok`` / ``paper_out_pre`` /
  ``paper_out_mid`` / ``print_failed`` to record what actually
  happened.
"""

from __future__ import annotations

from enum import StrEnum


class PrintedStatus(StrEnum):
    NOT_REQUESTED = "not_requested"
    PRINTED_OK = "printed_ok"
    PAPER_OUT_PRE = "paper_out_pre"
    PAPER_OUT_MID = "paper_out_mid"
    PRINT_FAILED = "print_failed"
