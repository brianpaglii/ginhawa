"""Thermal printer service (Xprinter XP-58IIH ESC/POS over USB).

Prints a citizen's session report at the end of a kiosk visit. Per
CLAUDE.md, receipts print in **one** language — the one selected at
session start — never mixed. The GUI's language toggle drives a
``Literal['en', 'tl']`` value that the FSM passes through to
:meth:`PrinterService.print_session_report`. We deliberately do NOT
persist this choice on the ``Session`` row; language is a presentation
concern, not part of the cloud-sync contract.

Architecture
------------
* :class:`PrinterService` is the abstract surface the FSM consumes.
* :class:`XprinterPrinterService` is the production implementation,
  wrapping ``python-escpos``.
* :class:`MockPrinterService` is the development / test implementation
  that records every "print" to ``mock_print_history`` for assertion
  in tests.
* :func:`create_printer_service` selects between them via
  ``Settings.MOCK_HARDWARE`` — never sniffed from env vars or
  ``platform.machine()`` directly (CLAUDE.md: one switch, one truth).

CLAUDE.md absolute rules (enforced here and elsewhere)
------------------------------------------------------
* The printer is NEVER powered from the Pi's USB rail or 5 V GPIO.
  It uses its own 9 V external adapter. Sharing power causes Pi
  brownouts during high-density print lines. (Hardware-side rule;
  no software enforcement is possible — documented for the operator.)
* A print failure is best-effort: the session record is saved
  regardless of whether the receipt prints. ``PrintedStatus`` records
  what actually happened so the cloud-side audit can distinguish
  "no paper at start" from "ran out mid-print" from "printer hung".
* ``services.audit.record_audit`` is the single sanctioned writer for
  audit rows; this module does NOT write its own. The FSM writes one
  ``fsm.print_complete`` (or ``fsm.paper_out_pre`` /
  ``fsm.paper_out_mid``) audit row from its after-callback once we
  return — keeping printer-side behaviour out of the audit-by-layer
  contract (services/audit.py).

Format (58 mm thermal, ~32 chars per line)
------------------------------------------

English (``language='en'``)::

    GINHAWA HEALTH MONITORING KIOSK
    --------------------------------
    Date: 2026-05-03 14:23
    Name: Maria Dela Cruz
    Age: 42  | Sex: F
    Barangay: San Roque
    --------------------------------
    MEASUREMENTS
    Systolic BP   : 128 mmHg
    ...
    BMI           : 24.4 (Normal)
    --------------------------------
    Thank you for your visit.

    (Not a medical diagnosis —
     health-monitoring guidance only.)

Tagalog (``language='tl'``)::

    GINHAWA HEALTH MONITORING KIOSK
    --------------------------------
    Petsa: 2026-05-03 14:23
    Pangalan: Maria Dela Cruz
    Edad: 42  | Kasarian: F
    Barangay: San Roque
    --------------------------------
    MGA SUKAT
    Sistoliko     : 128 mmHg
    ...
    BMI           : 24.4 (Normal)
    --------------------------------
    Salamat sa pagpapatingin!
    Maraming salamat po.

    (Hindi medikal na diagnosis —
     gabay lamang sa kalusugan.)

The non-diagnostic footer reflects the project's positioning
(see CLAUDE.md): the kiosk is a health-monitoring aid, not a
medical device. It appears in both languages.
"""

from __future__ import annotations

import asyncio
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal, NamedTuple

import structlog

if TYPE_CHECKING:
    from ..core.config import Settings
    from ..db.models import Citizen, Measurement, Session

_log = structlog.get_logger(__name__)


Language = Literal["en", "tl"]


class PrintedStatus(StrEnum):
    NOT_REQUESTED = "not_requested"
    PRINTED_OK = "printed_ok"
    PAPER_OUT_PRE = "paper_out_pre"
    PAPER_OUT_MID = "paper_out_mid"
    PRINT_FAILED = "print_failed"


class PrintResult(NamedTuple):
    success: bool
    printed_status: PrintedStatus


# Page width for the 58 mm Xprinter at the default font (Font A,
# 12×24 dot matrix). 32 chars per line is the published spec; we keep
# rendered lines below that to leave room for the unit suffix.
_LINE_WIDTH = 32
_DIVIDER = "-" * _LINE_WIDTH


# Render order matches the typical clinical reporting sequence on a
# kiosk receipt: vitals first, then anthropometrics, then derived BMI.
_MEASUREMENT_ORDER: tuple[str, ...] = (
    "systolic_bp",
    "diastolic_bp",
    "heart_rate",
    "spo2",
    "temperature",
    "height",
    "weight",
    "bmi",
)


# Per-language label and copy table. Centralised so adding a new
# language is one block at the bottom; missing keys fail at import
# time rather than producing a half-translated receipt.
#
# Tagalog measurement labels favour terms used in Philippine clinic
# settings (Sistoliko / Diastoliko are widely used loanwords; SpO2 and
# BMI stay as-is because they're universal abbreviations).
@dataclass(frozen=True)
class _Strings:
    header: str
    date_label: str
    name_label: str
    age_label: str
    sex_label: str
    barangay_label: str
    measurements_section: str
    no_measurements: tuple[str, ...]
    closing: tuple[str, ...]
    footer: tuple[str, ...]
    bmi_categories: tuple[str, str, str, str]  # (under, normal, over, obese)
    measurement_labels: dict[str, str]


_STRINGS: dict[Language, _Strings] = {
    "en": _Strings(
        header="GINHAWA HEALTH MONITORING KIOSK",
        date_label="Date",
        name_label="Name",
        age_label="Age",
        sex_label="Sex",
        barangay_label="Barangay",
        measurements_section="MEASUREMENTS",
        no_measurements=("No measurements captured. Please consult the BHW.",),
        closing=("Thank you for your visit.",),
        footer=(
            "(Not a medical diagnosis —",
            " health-monitoring guidance only.)",
        ),
        bmi_categories=("Underweight", "Normal", "Overweight", "Obese"),
        measurement_labels={
            "systolic_bp": "Systolic BP",
            "diastolic_bp": "Diastolic BP",
            "heart_rate": "Heart Rate",
            "spo2": "SpO2",
            "temperature": "Temperature",
            "height": "Height",
            "weight": "Weight",
            "bmi": "BMI",
        },
    ),
    "tl": _Strings(
        header="GINHAWA HEALTH MONITORING KIOSK",
        date_label="Petsa",
        name_label="Pangalan",
        age_label="Edad",
        sex_label="Kasarian",
        barangay_label="Barangay",
        measurements_section="MGA SUKAT",
        no_measurements=("Walang nakuhang sukat. Sumangguni sa BHW.",),
        closing=("Salamat sa pagpapatingin!", "Maraming salamat po."),
        footer=(
            "(Hindi medikal na diagnosis —",
            " gabay lamang sa kalusugan.)",
        ),
        bmi_categories=("Kulang sa Timbang", "Normal", "Sobra sa Timbang", "Obeso"),
        measurement_labels={
            "systolic_bp": "Sistoliko",
            "diastolic_bp": "Diastoliko",
            "heart_rate": "Tibok ng Puso",
            "spo2": "SpO2",
            "temperature": "Temperatura",
            "height": "Taas",
            "weight": "Timbang",
            "bmi": "BMI",
        },
    ),
}


def _format_value(measurement_type: str, value: float, unit: str) -> str:
    # Integer rendering for BP / HR / SpO2; one decimal for temperature
    # / height / weight / BMI. Matches what citizens see on the GUI
    # report screen.
    if measurement_type in ("systolic_bp", "diastolic_bp", "heart_rate", "spo2"):
        return f"{int(round(value))} {unit}".rstrip()
    return f"{value:.1f} {unit}".rstrip()


def _bmi_category(bmi: float, strings: _Strings) -> str:
    # WHO adult cut-offs. The kiosk surfaces these as health-monitoring
    # guidance, not a diagnosis (CLAUDE.md framing). Values outside
    # the validation range never reach this function.
    under, normal, over, obese = strings.bmi_categories
    if bmi < 18.5:
        return under
    if bmi < 25.0:
        return normal
    if bmi < 30.0:
        return over
    return obese


@dataclass
class _ReportContext:
    """Pre-rendered fields collected from session/citizen/measurements.

    Kept separate from the printer-driver code so the formatting layer
    is unit-testable without spinning up an Escpos handle.
    """

    citizen_name: str
    citizen_age: int | None
    citizen_sex: str
    citizen_barangay: str
    session_date: str
    valid_measurements: list[tuple[str, str]] = field(default_factory=list)


def _compute_age(dob_iso: str, as_of: datetime) -> int | None:
    # Date-of-birth is stored as ISO 8601 ("YYYY-MM-DD"). A malformed
    # entry shouldn't crash the print job — the receipt simply omits
    # the age line.
    try:
        dob = datetime.fromisoformat(dob_iso).date()
    except ValueError:
        return None
    years = as_of.year - dob.year
    if (as_of.month, as_of.day) < (dob.month, dob.day):
        years -= 1
    return years if years >= 0 else None


def _build_context(
    session: Session,
    citizen: Citizen,
    measurements: list[Measurement],
    strings: _Strings,
) -> _ReportContext:
    now = datetime.now()
    ctx = _ReportContext(
        citizen_name=citizen.full_name,
        citizen_age=_compute_age(citizen.dob, now),
        citizen_sex=citizen.sex,
        citizen_barangay=citizen.barangay,
        session_date=_session_timestamp(session, now),
    )

    # Index captured measurements by type; only valid (is_valid=1) rows
    # are eligible for the receipt. Out-of-range readings are recorded
    # in the DB but excluded from the citizen-facing print so we don't
    # promote a flagged value as if it were clinically meaningful.
    by_type: dict[str, Measurement] = {}
    for m in measurements:
        if not _is_valid_for_print(m):
            continue
        # If the same type appears more than once (e.g., two systolic
        # readings due to a re-cuff), the last one wins — matches the
        # GUI report screen, which always shows the most recent value.
        by_type[m.type] = m

    rendered_pairs: list[tuple[int, tuple[str, str]]] = []

    # Compute BMI on the fly when both height + weight are available
    # AND no BMI row already exists in the captured set. The schema
    # has an explicit ``bmi`` measurement type, but we don't require
    # the FSM to have written one — the printer can derive it.
    if "bmi" not in by_type and "height" in by_type and "weight" in by_type:
        derived = _try_derive_bmi(by_type["height"], by_type["weight"], strings)
        if derived is not None:
            rendered_pairs.append((_MEASUREMENT_ORDER.index("bmi"), derived))

    for type_name in _MEASUREMENT_ORDER:
        captured = by_type.get(type_name)
        if captured is None:
            continue
        label = strings.measurement_labels[type_name]
        rendered = _format_value(captured.type, float(captured.value), captured.unit)
        if type_name == "bmi":
            rendered = f"{rendered} ({_bmi_category(float(captured.value), strings)})"
        rendered_pairs.append((_MEASUREMENT_ORDER.index(type_name), (label, rendered)))

    rendered_pairs.sort(key=lambda kv: kv[0])
    ctx.valid_measurements = [pair for _, pair in rendered_pairs]
    return ctx


def _is_valid_for_print(m: Measurement) -> bool:
    return bool(m.is_valid)


def _try_derive_bmi(
    height: Measurement,
    weight: Measurement,
    strings: _Strings,
) -> tuple[str, str] | None:
    # Defensive: only derive when the units match what validation.py
    # expects (cm + kg). Mismatched units mean a sensor wrote
    # something we don't know how to convert — better to omit BMI
    # than to print a wrong number.
    if height.unit != "cm" or weight.unit != "kg":
        return None
    h_m = float(height.value) / 100.0
    if h_m <= 0:
        return None
    bmi = float(weight.value) / (h_m * h_m)
    if not math.isfinite(bmi) or not (10.0 <= bmi <= 60.0):
        return None
    rendered = f"{bmi:.1f} ({_bmi_category(bmi, strings)})"
    return (strings.measurement_labels["bmi"], rendered)


def _session_timestamp(session: Session, fallback_now: datetime) -> str:
    """Format session.started_at for the receipt header.

    The DB stores started_at as a UTC-aware ISO string (per the
    project's "all timestamps stored UTC" rule). The cuff and GUI
    run in the deployment's local timezone (Asia/Manila in
    practice; the Pi's tz is configured via ``timedatectl``). We
    convert to local time here so the citizen sees the wall-clock
    time they took the measurement, not UTC — bench evidence
    (2026-05-08): a 19:25 PHT session printed "11:25" on the
    receipt before this fix.

    ``.astimezone()`` with no argument resolves the host's local
    tz via ``/etc/localtime``. The fallback path uses
    ``fallback_now`` which is already a naive-local
    ``datetime.now()``, so it's left alone.
    """
    raw = session.started_at
    try:
        dt = datetime.fromisoformat(raw).astimezone()
    except ValueError:
        dt = fallback_now
    return dt.strftime("%Y-%m-%d %H:%M")


def _render_lines(ctx: _ReportContext, strings: _Strings) -> list[str]:
    lines: list[str] = []
    lines.append(strings.header)
    lines.append(_DIVIDER)
    lines.append(f"{strings.date_label}: {ctx.session_date}")
    lines.append(f"{strings.name_label}: {ctx.citizen_name}")
    age_str = str(ctx.citizen_age) if ctx.citizen_age is not None else "-"
    lines.append(
        f"{strings.age_label}: {age_str}  | {strings.sex_label}: {ctx.citizen_sex}"
    )
    lines.append(f"{strings.barangay_label}: {ctx.citizen_barangay}")
    lines.append(_DIVIDER)
    lines.append(strings.measurements_section)
    if ctx.valid_measurements:
        # Two-column rendering: 13-char left-padded label, " : ",
        # right-side value. Within 32 chars on the strip even for the
        # longest unit suffix in our set ("kg/m^2").
        for label, value in ctx.valid_measurements:
            lines.append(f"{label:<13} : {value}")
    else:
        lines.extend(strings.no_measurements)
    lines.append(_DIVIDER)
    lines.extend(strings.closing)
    lines.append("")
    lines.extend(strings.footer)
    return lines


class PrinterService(ABC):
    """Abstract printer surface consumed by the FSM's PRINTING state."""

    @abstractmethod
    def is_paper_present(self) -> bool:
        """Return True if the printer reports paper loaded.

        The Xprinter XP-58IIH exposes a near-end and an out-of-paper
        sensor. We consider both "near-end" and "ok" as paper-present
        (a near-end strip can still complete a single citizen receipt);
        only true out-of-paper returns False. Hardware errors (USB
        disconnect, query timeout) also return False — the FSM treats
        them the same way as no paper for the pre-print branch.
        """

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if the printer's USB device is reachable.

        Distinct from ``is_paper_present``: a printer can be online
        with no paper, or offline regardless of paper. Both
        conditions block printing; the FSM uses
        ``is_paper_present`` to choose the ``paper_out_pre`` audit
        path versus ``print_failed``.
        """

    @abstractmethod
    async def print_session_report(
        self,
        session: Session,
        citizen: Citizen,
        measurements: list[Measurement],
        language: Language,
    ) -> PrintResult:
        """Render and print a session report in the chosen language.

        ``language`` is sourced from the GUI's session-start toggle and
        threaded through the FSM. The receipt is rendered ENTIRELY in
        that language (CLAUDE.md: "Receipts print in the language
        selected at session start" — no mixed-language pages). It is
        NOT persisted on the ``Session`` row; if a re-print is ever
        required after a re-commission the GUI will re-supply the
        choice.

        Returns a :class:`PrintResult` whose ``printed_status``
        reports what actually happened. A print failure is non-fatal
        — the FSM finalises the session row regardless and records
        the printed_status verbatim.
        """


class EscPosPrinterService(PrinterService):
    """Production driver for any ESC/POS thermal printer over USB.

    Originally written against the Xprinter XP-58IIH; the 2026-05-04
    bench test on an STM-based generic 58 mm clone surfaced two
    portability issues that this class now papers over:

    1. Some printers don't respond to bidirectional ESC/POS status
       queries (DLE EOT n / GS r n). When ``supports_status_query``
       is False the service skips paper-status reads entirely and
       assumes paper is present — best-effort printing.
    2. python-escpos auto-detects USB endpoints from the descriptor,
       but generic clones expose them at non-standard addresses
       (typically IN at ``0x81``, OUT at ``0x01``). Pass explicit
       ``in_endpoint`` / ``out_endpoint`` integers to override the
       library's guess.

    Each call to :meth:`print_session_report` opens a fresh USB
    handle, prints the receipt, and closes the handle. We do NOT
    keep the handle open across sessions: a long-lived handle blocks
    the device from recovering after a USB hub power glitch and
    silently fails the next print. Open/close per-job is slower but
    self-healing.

    Concurrency: an ``asyncio.Lock`` serialises ``print_session_report``
    calls so a wiring bug that fires two prints in parallel can't
    corrupt the device. The FSM should never invoke this concurrently
    in production — the lock is defence-in-depth.
    """

    def __init__(
        self,
        vendor_id: int,
        product_id: int,
        in_endpoint: int | None = None,
        out_endpoint: int | None = None,
        supports_status_query: bool = True,
        profile: str | None = None,
    ) -> None:
        self._vendor_id = vendor_id
        self._product_id = product_id
        self._in_endpoint = in_endpoint
        self._out_endpoint = out_endpoint
        self._supports_status_query = supports_status_query
        self._profile = profile
        self._print_lock = asyncio.Lock()

    def is_paper_present(self) -> bool:
        if not self._supports_status_query:
            # Hardware doesn't expose ESC/POS DLE EOT / GS r. There's
            # no portable way to ask "is paper loaded?" without it,
            # so we assume yes and let the print attempt fail visibly
            # if the roll is empty. Operators can spot a paper-out by
            # checking the receipt tray.
            return True
        try:
            p = self._open_printer()
        except Exception as exc:
            _log.warning(
                "printer.paper_status_failed",
                error=type(exc).__name__,
                vendor_id=hex(self._vendor_id),
                product_id=hex(self._product_id),
            )
            return False
        try:
            # paper_status: 2 = adequate, 1 = near-end, 0 = no paper.
            return int(p.paper_status()) > 0
        except Exception as exc:
            _log.warning("printer.paper_status_failed", error=type(exc).__name__)
            return False
        finally:
            self._safe_close(p)

    def is_available(self) -> bool:
        try:
            p = self._open_printer()
        except Exception as exc:
            _log.warning(
                "printer.device_unavailable",
                error=type(exc).__name__,
                vendor_id=hex(self._vendor_id),
                product_id=hex(self._product_id),
            )
            return False
        self._safe_close(p)
        return True

    async def print_session_report(
        self,
        session: Session,
        citizen: Citizen,
        measurements: list[Measurement],
        language: Language,
    ) -> PrintResult:
        async with self._print_lock:
            return await self._print_session_report_locked(
                session, citizen, measurements, language
            )

    async def _print_session_report_locked(
        self,
        session: Session,
        citizen: Citizen,
        measurements: list[Measurement],
        language: Language,
    ) -> PrintResult:
        # Pre-flight: is paper loaded? When supports_status_query is
        # False this short-circuits to True; the print may still fail
        # with PAPER_OUT_MID (best-effort path).
        if not self.is_paper_present():
            _log.info("printer.paper_out_pre", session_id=session.id)
            return PrintResult(False, PrintedStatus.PAPER_OUT_PRE)

        strings = _STRINGS[language]
        ctx = _build_context(session, citizen, measurements, strings)
        lines = _render_lines(ctx, strings)

        try:
            p = self._open_printer()
        except Exception as exc:
            _log.warning(
                "printer.print_failed",
                session_id=session.id,
                error=type(exc).__name__,
                error_msg=str(exc),
            )
            return PrintResult(False, PrintedStatus.PRINT_FAILED)

        try:
            for line in lines:
                p.text(line + "\n")
                # Mid-print check: re-query the paper sensor between
                # lines so we surface a paper-out failure as
                # PAPER_OUT_MID rather than PRINT_FAILED. Skipped on
                # printers that don't support the query — those go
                # straight from "printing" to PRINTED_OK or, on a
                # write failure, PRINT_FAILED.
                if self._supports_status_query and int(p.paper_status()) == 0:
                    _log.warning("printer.paper_out_mid", session_id=session.id)
                    return PrintResult(False, PrintedStatus.PAPER_OUT_MID)
            p.cut()
        except Exception as exc:
            _log.warning(
                "printer.print_failed",
                session_id=session.id,
                error=type(exc).__name__,
                error_msg=str(exc),
            )
            return PrintResult(False, PrintedStatus.PRINT_FAILED)
        finally:
            self._safe_close(p)

        _log.info(
            "printer.printed_ok",
            session_id=session.id,
            line_count=len(lines),
            language=language,
        )
        return PrintResult(True, PrintedStatus.PRINTED_OK)

    def _open_printer(self) -> Any:
        # Imported lazily so importing this module on a dev laptop
        # without libusb available doesn't fail at import time.
        # Returned as Any because python-escpos ships without type
        # stubs (see pyproject.toml mypy overrides).
        from escpos.printer import Usb

        kwargs: dict[str, Any] = {
            "idVendor": self._vendor_id,
            "idProduct": self._product_id,
        }
        # Only pass the endpoint kwargs when the deployer set them
        # explicitly — leaving them unset preserves python-escpos's
        # auto-detect path (correct for most Xprinter and Epson units).
        if self._in_endpoint is not None:
            kwargs["in_ep"] = self._in_endpoint
        if self._out_endpoint is not None:
            kwargs["out_ep"] = self._out_endpoint
        if self._profile is not None:
            kwargs["profile"] = self._profile
        return Usb(**kwargs)

    @staticmethod
    def _safe_close(p: Any) -> None:
        close = getattr(p, "close", None)
        if close is None:
            return
        try:
            close()
        except Exception as exc:
            _log.warning("printer.close_failed", error=type(exc).__name__)


# Backward-compat alias. Phase 2 Prompt 7 named the class
# ``XprinterPrinterService``; Phase 2 Prompt 7.1 generalised it to any
# ESC/POS printer over USB after the STM-based bench printer surfaced
# portability issues. Existing callers that import the old name keep
# working; rename in callers when convenient.
XprinterPrinterService = EscPosPrinterService


class MockPrinterService(PrinterService):
    """In-process mock used for tests and ``MOCK_HARDWARE=true``.

    Records every print job to ``mock_print_history`` (one entry per
    call to :meth:`print_session_report`) so tests can assert on the
    rendered text without a USB device.

    The ``paper_present`` and ``available`` flags are settable
    attributes — tests flip them to simulate paper-out / device-
    unavailable conditions.
    """

    def __init__(self) -> None:
        self.paper_present: bool = True
        self.available: bool = True
        # Each entry is the rendered receipt as a single string with
        # ``\n`` line separators — matches what the real driver pushes
        # to the printer.
        self.mock_print_history: list[str] = []
        # Paper-out-mid simulation: when set, the next print run stops
        # after this many lines and returns PAPER_OUT_MID. Auto-resets
        # to None after firing once.
        self.simulate_paper_out_after_lines: int | None = None
        # Print-failed simulation: when True, the next print run
        # raises ``PRINT_FAILED``. Auto-resets after firing once.
        self.simulate_print_failed: bool = False

    def is_paper_present(self) -> bool:
        return self.paper_present

    def is_available(self) -> bool:
        return self.available

    async def print_session_report(
        self,
        session: Session,
        citizen: Citizen,
        measurements: list[Measurement],
        language: Language,
    ) -> PrintResult:
        if not self.is_paper_present():
            _log.info("mock_printer.paper_out_pre", session_id=session.id)
            return PrintResult(False, PrintedStatus.PAPER_OUT_PRE)

        if self.simulate_print_failed:
            self.simulate_print_failed = False
            _log.info("mock_printer.print_failed_simulated", session_id=session.id)
            return PrintResult(False, PrintedStatus.PRINT_FAILED)

        strings = _STRINGS[language]
        ctx = _build_context(session, citizen, measurements, strings)
        lines = _render_lines(ctx, strings)

        cutoff = self.simulate_paper_out_after_lines
        if cutoff is not None and cutoff < len(lines):
            self.simulate_paper_out_after_lines = None
            partial = "\n".join(lines[:cutoff])
            self.mock_print_history.append(partial)
            _log.info(
                "mock_printer.paper_out_mid_simulated",
                session_id=session.id,
                lines_printed=cutoff,
            )
            return PrintResult(False, PrintedStatus.PAPER_OUT_MID)

        rendered = "\n".join(lines)
        self.mock_print_history.append(rendered)
        _log.info(
            "mock_printer.printed_ok",
            session_id=session.id,
            line_count=len(lines),
            language=language,
        )
        return PrintResult(True, PrintedStatus.PRINTED_OK)


def create_printer_service(settings: Settings) -> PrinterService:
    """Factory: pick the printer implementation per ``MOCK_HARDWARE``.

    The single sanctioned consumer of ``settings.MOCK_HARDWARE`` for
    the printer subsystem (CLAUDE.md: one switch, one truth). Threads
    the four hardware-portability knobs (endpoints, status-query
    support, python-escpos profile) into the production driver — see
    ``kiosk/docs/runbook.md`` "Printer hardware portability" for when
    each one needs overriding.
    """
    if settings.MOCK_HARDWARE:
        return MockPrinterService()
    return EscPosPrinterService(
        vendor_id=settings.KIOSK_PRINTER_VENDOR_ID,
        product_id=settings.KIOSK_PRINTER_PRODUCT_ID,
        in_endpoint=settings.KIOSK_PRINTER_USB_IN_ENDPOINT,
        out_endpoint=settings.KIOSK_PRINTER_USB_OUT_ENDPOINT,
        supports_status_query=settings.KIOSK_PRINTER_SUPPORTS_STATUS_QUERY,
        profile=settings.KIOSK_PRINTER_PROFILE,
    )
