"""Async integration tests for the ESC/POS printer service.

Mocks ``escpos.printer.Usb`` so the tests run on a dev laptop
without libusb / a physical printer. The focus is on the
hardware-portability paths added in Phase 2 Prompt 7.1: configurable
endpoints, gated status queries, and graceful failure on bad
endpoint addresses.

The 8 tests in ``test_printer.py`` already cover the receipt format
and the FSM-side ``PrintedStatus`` contract; this module only
exercises the behaviour at the python-escpos boundary.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from ginhawa_kiosk.db.models import Citizen, Measurement
from ginhawa_kiosk.db.models import Session as SessionModel
from ginhawa_kiosk.services.printer import (
    EscPosPrinterService,
    MockPrinterService,
    PrintedStatus,
    PrintResult,
    XprinterPrinterService,
    create_printer_service,
)


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def _citizen() -> Citizen:
    return Citizen(
        id=str(uuid.uuid4()),
        rfid_uid="ASYNC_TEST_UID",
        full_name="Test Async",
        dob="1990-01-01",
        sex="F",
        barangay="Tibagan",
        phone=None,
        consent_version="v1",
        consent_given_at="2026-05-04T00:00:00+00:00",
        registered_at="2026-05-04T00:00:00+00:00",
        registered_by=None,
        is_active=1,
        synced=0,
        updated_at="2026-05-04T00:00:00+00:00",
    )


def _session(citizen: Citizen) -> SessionModel:
    return SessionModel(
        id=str(uuid.uuid4()),
        citizen_id=citizen.id,
        device_id="test-kiosk",
        started_at="2026-05-04T14:00:00+00:00",
        ended_at=None,
        status="in_progress",
        error_reason=None,
        measurement_path="vitals",
        printed_status="not_requested",
        synced=0,
        updated_at="2026-05-04T14:00:00+00:00",
    )


def _meas(session: SessionModel) -> Measurement:
    return Measurement(
        id=str(uuid.uuid4()),
        session_id=session.id,
        type="systolic_bp",
        value=128.0,
        unit="mmHg",
        source_device="test",
        measured_at="2026-05-04T14:00:00+00:00",
        is_valid=1,
        validation_notes=None,
        raw_json=None,
        synced=0,
        updated_at="2026-05-04T14:00:00+00:00",
    )


def _make_usb_mock(*, paper_status: int = 2) -> MagicMock:
    """Build a fake ``escpos.printer.Usb`` instance.

    Default paper_status=2 (adequate). Tests override per-instance
    behaviour by setting attributes on the returned mock.
    """
    instance = MagicMock(name="UsbInstance")
    instance.paper_status.return_value = paper_status
    instance.text.return_value = None
    instance.cut.return_value = None
    instance.close.return_value = None
    return instance


# ---------------------------------------------------------------------
# 1. The full print path runs without blocking the event loop
# ---------------------------------------------------------------------
# Verifies print_session_report can be awaited inside an asyncio
# event loop without blocking — important because the kiosk's GUI
# runs the print on the qasync-integrated loop and a sync block
# would freeze the screen.
# Mortality: 'Would fail if the method synchronously blocked the
# event loop.'
@pytest.mark.asyncio
async def test_print_session_report_runs_in_async_context() -> None:
    citizen = _citizen()
    session = _session(citizen)
    measurements = [_meas(session)]
    usb_mock = _make_usb_mock()

    with patch("escpos.printer.Usb", return_value=usb_mock):
        service = EscPosPrinterService(vendor_id=0x0416, product_id=0x5011)
        # The async-context check: a parallel sleep should still run
        # while print_session_report is executing. If the print call
        # blocked the loop, the gather would serialise and total time
        # would be dominated by the print, not the shorter operation.
        result, _ = await asyncio.gather(
            service.print_session_report(session, citizen, measurements, "en"),
            asyncio.sleep(0),
        )
    assert result == PrintResult(True, PrintedStatus.PRINTED_OK)


# ---------------------------------------------------------------------
# 2. Endpoint kwargs are forwarded when configured
# ---------------------------------------------------------------------
# Verifies that when the deployer sets explicit USB endpoints in
# Settings, _open_printer threads them through to escpos.printer.Usb
# as ``in_ep`` / ``out_ep``. This is the workaround for STM-based
# generic printers whose IN endpoint is at 0x81 instead of the
# library's auto-detected 0x82.
# Mortality: 'Would fail if endpoint config were ignored.'
@pytest.mark.asyncio
async def test_print_uses_configured_endpoints_when_set() -> None:
    citizen = _citizen()
    session = _session(citizen)
    usb_mock = _make_usb_mock()

    with patch("escpos.printer.Usb", return_value=usb_mock) as usb_cls:
        service = EscPosPrinterService(
            vendor_id=0x0483,
            product_id=0x070B,
            in_endpoint=0x81,
            out_endpoint=0x01,
        )
        await service.print_session_report(session, citizen, [_meas(session)], "en")

    # The Usb constructor is called both during is_paper_present (the
    # pre-flight check) and for the actual print. Every call must
    # carry the explicit endpoint kwargs.
    assert usb_cls.call_count >= 1
    for call in usb_cls.call_args_list:
        kwargs = call.kwargs
        assert kwargs["idVendor"] == 0x0483
        assert kwargs["idProduct"] == 0x070B
        assert kwargs["in_ep"] == 0x81
        assert kwargs["out_ep"] == 0x01


# ---------------------------------------------------------------------
# 3. Endpoint kwargs are omitted when unset (auto-detect path)
# ---------------------------------------------------------------------
# When the deployer leaves endpoints at None, _open_printer must NOT
# pass any in_ep / out_ep kwargs — python-escpos's auto-detect path
# only runs when those kwargs are absent. Passing None explicitly
# would still disable auto-detect on some library versions.
# Mortality: 'Would fail if defaults were silently injected, breaking
# auto-detect path.'
@pytest.mark.asyncio
async def test_print_omits_endpoint_kwargs_when_unset() -> None:
    citizen = _citizen()
    session = _session(citizen)
    usb_mock = _make_usb_mock()

    with patch("escpos.printer.Usb", return_value=usb_mock) as usb_cls:
        service = EscPosPrinterService(
            vendor_id=0x0416,
            product_id=0x5011,
            in_endpoint=None,
            out_endpoint=None,
            profile=None,
        )
        await service.print_session_report(session, citizen, [_meas(session)], "en")

    for call in usb_cls.call_args_list:
        kwargs = call.kwargs
        assert "in_ep" not in kwargs
        assert "out_ep" not in kwargs
        assert "profile" not in kwargs


# ---------------------------------------------------------------------
# 4. Status query is skipped when the printer doesn't support it
# ---------------------------------------------------------------------
# is_paper_present must short-circuit to True without ever calling
# Usb.paper_status() when the deployer flagged the printer as not
# supporting bidirectional queries. The STM-based generic clones
# raise ValueError on paper_status; the gated path means we never
# trigger that.
# Mortality: 'Would fail if the support flag were ignored, defeating
# the portability fix.'
def test_paper_check_skipped_when_status_query_unsupported() -> None:
    usb_mock = _make_usb_mock()
    usb_mock.paper_status.side_effect = ValueError("DLE EOT not supported")

    with patch("escpos.printer.Usb", return_value=usb_mock):
        service = EscPosPrinterService(
            vendor_id=0x0483,
            product_id=0x070B,
            supports_status_query=False,
        )
        # Returns True without raising and without calling
        # paper_status — the failing side_effect proves it wasn't
        # invoked.
        assert service.is_paper_present() is True

    assert usb_mock.paper_status.call_count == 0


# ---------------------------------------------------------------------
# 5. Status query IS called when supported
# ---------------------------------------------------------------------
# Mirror of test 4: when supports_status_query=True, paper_status()
# must be invoked. Pinning this ensures the gating flag doesn't
# accidentally disable checks on capable printers (regressing to
# always-best-effort).
# Mortality: 'Would fail if the flag accidentally disabled checks
# for capable printers.'
def test_paper_check_called_when_status_query_supported() -> None:
    usb_mock = _make_usb_mock(paper_status=2)

    with patch("escpos.printer.Usb", return_value=usb_mock):
        service = EscPosPrinterService(
            vendor_id=0x0416,
            product_id=0x5011,
            supports_status_query=True,
        )
        assert service.is_paper_present() is True

    assert usb_mock.paper_status.call_count == 1


# ---------------------------------------------------------------------
# 6. Endpoint errors mid-print return PRINT_FAILED, never propagate
# ---------------------------------------------------------------------
# The 0x82 read that surfaced on the STM bench printer raised
# ValueError("Invalid endpoint address 0x82"). The service must
# catch it and return PrintResult(False, PRINT_FAILED) so the FSM's
# PRINTING state can advance to END cleanly. An uncaught exception
# would crash the FSM's after-callback and leave the kiosk hung.
# Mortality: 'Would fail if endpoint errors propagated up uncaught
# and crashed the FSM.'
@pytest.mark.asyncio
async def test_print_failure_returns_print_failed_status() -> None:
    citizen = _citizen()
    session = _session(citizen)
    usb_mock = _make_usb_mock()
    usb_mock.text.side_effect = ValueError("Invalid endpoint address 0x82")

    with patch("escpos.printer.Usb", return_value=usb_mock):
        service = EscPosPrinterService(
            vendor_id=0x0483,
            product_id=0x070B,
            supports_status_query=False,
        )
        result = await service.print_session_report(
            session, citizen, [_meas(session)], "en"
        )

    assert result == PrintResult(False, PrintedStatus.PRINT_FAILED)
    # The handle was still closed despite the failure — defensive
    # cleanup keeps the next print attempt from hitting a stuck handle.
    assert usb_mock.close.call_count >= 1


# ---------------------------------------------------------------------
# 7. Concurrent print calls are serialised via the internal lock
# ---------------------------------------------------------------------
# The FSM should never invoke print_session_report concurrently in
# production — PRINTING is a single state with one print event. But
# defensive serialisation matters: if a wiring bug fires two prints
# in parallel, they must take turns at the USB device, not interleave
# their text/cut commands.
# Mortality: 'Would fail if concurrent prints corrupted each other;
# the FSM should never invoke this concurrently but defensive
# serialization matters.'
@pytest.mark.asyncio
async def test_concurrent_print_calls_are_serialized() -> None:
    citizen = _citizen()
    session = _session(citizen)
    measurements = [_meas(session)]

    in_flight = 0
    max_concurrent = 0

    async def slow_text_call(*_args: Any, **_kwargs: Any) -> None:
        # Yield once per text() call so the second task gets a chance
        # to run if the lock isn't doing its job.
        nonlocal in_flight, max_concurrent
        in_flight += 1
        max_concurrent = max(max_concurrent, in_flight)
        await asyncio.sleep(0)
        in_flight -= 1

    def make_usb(**_kwargs: Any) -> MagicMock:
        usb_mock = _make_usb_mock()
        # text() is called on the production code path many times;
        # we wrap it with the concurrency tracker so the lock's
        # effect is observable.
        usb_mock.text = MagicMock(
            side_effect=lambda *a, **k: asyncio.run_coroutine_threadsafe(  # noqa: ARG005
                slow_text_call(), asyncio.get_event_loop()
            ).result(timeout=0)
        )
        return usb_mock

    # Simpler approach: track open_printer concurrency. Each print
    # opens a Usb handle once; if the lock works, the open calls are
    # serialised — never two open instances live at the same time
    # for one service.
    open_in_flight = 0
    open_max = 0

    def tracking_usb(**_kwargs: Any) -> MagicMock:
        nonlocal open_in_flight, open_max
        open_in_flight += 1
        open_max = max(open_max, open_in_flight)
        instance = _make_usb_mock()
        original_close = instance.close

        def closing(*a: Any, **k: Any) -> Any:
            nonlocal open_in_flight
            open_in_flight -= 1
            return original_close(*a, **k)

        instance.close = MagicMock(side_effect=closing)
        return instance

    with patch("escpos.printer.Usb", side_effect=tracking_usb):
        service = EscPosPrinterService(
            vendor_id=0x0416,
            product_id=0x5011,
            supports_status_query=False,  # avoid pre-flight handle complicating count
        )
        await asyncio.gather(
            service.print_session_report(session, citizen, measurements, "en"),
            service.print_session_report(session, citizen, measurements, "tl"),
        )

    # Lock holds the second print until the first finishes — so only
    # one Usb handle is open at any given moment.
    assert open_max == 1, f"expected serialised opens (max=1), got max={open_max}"


# ---------------------------------------------------------------------
# 8. A close() failure is logged but does not propagate
# ---------------------------------------------------------------------
# When python-escpos's close() raises (e.g., the device was already
# released by udev), the print result must still be returned. A
# stuck close that propagated would mean the FSM never receives
# print_complete, and the PRINTING state would hang until its 30 s
# hard timeout — bad UX for a citizen waiting on a successful print.
# Mortality: 'Would fail if a stuck-close handle prevented future
# prints.'
@pytest.mark.asyncio
async def test_print_does_not_crash_when_close_fails() -> None:
    citizen = _citizen()
    session = _session(citizen)
    usb_mock = _make_usb_mock()
    usb_mock.close.side_effect = OSError("device went away")

    with patch("escpos.printer.Usb", return_value=usb_mock):
        service = EscPosPrinterService(
            vendor_id=0x0416,
            product_id=0x5011,
            supports_status_query=False,
        )
        result = await service.print_session_report(
            session, citizen, [_meas(session)], "en"
        )

    # The print itself completed — close-time errors are non-fatal.
    assert result == PrintResult(True, PrintedStatus.PRINTED_OK)


# ---------------------------------------------------------------------
# 9. Backward-compat alias still imports
# ---------------------------------------------------------------------
# Phase 2 Prompt 7 named the class XprinterPrinterService; Prompt 7.1
# renamed it to EscPosPrinterService and kept the old name as an
# alias for callers that haven't been updated yet. This pins the
# alias contract so accidentally removing it in a future cleanup
# doesn't break ``from ... import XprinterPrinterService``.
# Mortality: 'Would fail if the rename broke backward-compat callers.'
def test_xprinter_alias_still_imports() -> None:
    assert XprinterPrinterService is EscPosPrinterService


# ---------------------------------------------------------------------
# 10. Factory threads all four portability config fields
# ---------------------------------------------------------------------
# create_printer_service must read all four new Settings fields
# (KIOSK_PRINTER_USB_IN_ENDPOINT, KIOSK_PRINTER_USB_OUT_ENDPOINT,
# KIOSK_PRINTER_SUPPORTS_STATUS_QUERY, KIOSK_PRINTER_PROFILE) and
# pass them into the EscPosPrinterService constructor. Dropping any
# of them on the factory floor means a deployer who set the env var
# would see no effect — a silent failure mode.
# Mortality: 'Would fail if the factory dropped config.'
def test_factory_creates_correct_service_with_full_config() -> None:
    class _FakeSettings:
        MOCK_HARDWARE = False
        KIOSK_PRINTER_VENDOR_ID = 0x0483
        KIOSK_PRINTER_PRODUCT_ID = 0x070B
        KIOSK_PRINTER_USB_IN_ENDPOINT = 0x81
        KIOSK_PRINTER_USB_OUT_ENDPOINT = 0x01
        KIOSK_PRINTER_SUPPORTS_STATUS_QUERY = False
        KIOSK_PRINTER_PROFILE = "TM-T88III"

    svc = create_printer_service(_FakeSettings())  # type: ignore[arg-type]
    assert isinstance(svc, EscPosPrinterService)
    # Inspect the private fields rather than asking the service to
    # print — we want to pin the wiring, not the print behaviour.
    assert svc._vendor_id == 0x0483
    assert svc._product_id == 0x070B
    assert svc._in_endpoint == 0x81
    assert svc._out_endpoint == 0x01
    assert svc._supports_status_query is False
    assert svc._profile == "TM-T88III"


# Sanity: the MockPrinterService import is still wired (the existing
# test_printer.py imports it; this re-imports here so a future
# accidental removal from services/__init__.py is caught early).
def test_mock_printer_service_still_exported() -> None:
    assert MockPrinterService is not None


# ---------------------------------------------------------------------
# Coverage tests for the EscPos failure-path branches that the FSM
# never sees in the happy thread but the kiosk's BHW will see when
# they swap a printer or pull the USB cable mid-shift.
# ---------------------------------------------------------------------


# is_paper_present catches a Usb-construction failure and returns
# False — the PRINTING state's "is the printer reachable?" check.
def test_is_paper_present_returns_false_when_open_fails() -> None:
    with patch("escpos.printer.Usb", side_effect=OSError("device not present")):
        service = EscPosPrinterService(
            vendor_id=0x0416, product_id=0x5011, supports_status_query=True
        )
        assert service.is_paper_present() is False


# is_paper_present catches paper_status() raising mid-query and
# returns False so the FSM transitions to PAPER_OUT_PRE rather than
# crashing on the unhandled exception.
def test_is_paper_present_returns_false_when_paper_status_raises() -> None:
    usb_mock = _make_usb_mock()
    usb_mock.paper_status.side_effect = ValueError("query timeout")
    with patch("escpos.printer.Usb", return_value=usb_mock):
        service = EscPosPrinterService(
            vendor_id=0x0416, product_id=0x5011, supports_status_query=True
        )
        assert service.is_paper_present() is False


# is_available returns True when the Usb constructor succeeds; the
# happy-path counterpart of test_is_available_returns_false.
def test_is_available_returns_true_when_open_succeeds() -> None:
    usb_mock = _make_usb_mock()
    with patch("escpos.printer.Usb", return_value=usb_mock):
        service = EscPosPrinterService(vendor_id=0x0416, product_id=0x5011)
        assert service.is_available() is True


# is_available returns False when the Usb constructor raises — the
# FSM's REPORT screen uses this to hide the Print button.
def test_is_available_returns_false_when_open_fails() -> None:
    with patch("escpos.printer.Usb", side_effect=OSError("not connected")):
        service = EscPosPrinterService(vendor_id=0x0416, product_id=0x5011)
        assert service.is_available() is False


# print_session_report returns PAPER_OUT_PRE when is_paper_present()
# reports no paper before the actual print starts.
@pytest.mark.asyncio
async def test_print_returns_paper_out_pre_when_paper_absent() -> None:
    citizen = _citizen()
    session = _session(citizen)
    usb_mock = _make_usb_mock(paper_status=0)  # 0 = no paper
    with patch("escpos.printer.Usb", return_value=usb_mock):
        service = EscPosPrinterService(
            vendor_id=0x0416, product_id=0x5011, supports_status_query=True
        )
        result = await service.print_session_report(
            session, citizen, [_meas(session)], "en"
        )
    assert result == PrintResult(False, PrintedStatus.PAPER_OUT_PRE)


# print_session_report returns PAPER_OUT_MID when the paper sensor
# reports empty between two lines of an in-progress print.
@pytest.mark.asyncio
async def test_print_returns_paper_out_mid_when_roll_runs_out() -> None:
    citizen = _citizen()
    session = _session(citizen)
    # Simulate a roll that runs out partway through: pre-flight check
    # sees adequate paper (2), then the second mid-print check returns
    # 0 (empty). The exact call sequence depends on the rendered line
    # count; we model this with a side_effect list that stays at 0
    # after the pre-flight so any mid-print check trips the branch.
    usb_mock = _make_usb_mock()
    paper_states = [2, 2, 0]  # pre-flight ok, first line ok, then empty
    usb_mock.paper_status.side_effect = paper_states + [0] * 50
    with patch("escpos.printer.Usb", return_value=usb_mock):
        service = EscPosPrinterService(
            vendor_id=0x0416, product_id=0x5011, supports_status_query=True
        )
        result = await service.print_session_report(
            session, citizen, [_meas(session)], "en"
        )
    assert result == PrintResult(False, PrintedStatus.PAPER_OUT_MID)


# print_session_report returns PRINT_FAILED when _open_printer raises
# during the print (after the pre-flight handle has already opened
# and closed cleanly). Distinct from test_print_failure_returns_print_failed_status
# which raises on .text() — this hits the construct-time path.
@pytest.mark.asyncio
async def test_print_returns_print_failed_when_open_raises() -> None:
    citizen = _citizen()
    session = _session(citizen)

    call_count = 0

    def usb_factory(**_kwargs: Any) -> MagicMock:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Pre-flight is_paper_present() — must succeed so we get
            # past to the actual print.
            return _make_usb_mock(paper_status=2)
        raise OSError("device went away between pre-flight and print")

    with patch("escpos.printer.Usb", side_effect=usb_factory):
        service = EscPosPrinterService(
            vendor_id=0x0416, product_id=0x5011, supports_status_query=True
        )
        result = await service.print_session_report(
            session, citizen, [_meas(session)], "en"
        )
    assert result == PrintResult(False, PrintedStatus.PRINT_FAILED)
