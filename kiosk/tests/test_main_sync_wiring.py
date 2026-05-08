"""Smoke test: __main__ wires SyncDaemon + CloudClient into boot.

The full ``main()`` is hardware-bound (qasync + QApplication +
SQLCipher engine + sensor BLE/MQTT clients) and pragma-no-cover by
design — running it in a unit-test process would either crash
without a display or block on a real cloud HTTPS call. What we
*can* assert without booting the kiosk is that the boot module
imports the sync surface, constructs both pieces, and schedules
``SyncDaemon.run()`` on the qasync loop.

This test reads ``__main__.py`` source plus its module attributes;
that's deliberately structural, not behavioural. Behavioural
coverage of the daemon itself lives in ``tests/sync/test_daemon.py``;
this one's job is to keep the wiring from silently regressing back
to "33 unsynced sessions on the kiosk forever".
"""

from __future__ import annotations

import inspect

from ginhawa_kiosk import __main__ as kiosk_main


# Verifies the sync-side imports landed at the module level. If a
# future refactor accidentally drops the import, the test catches
# it before the daemon goes silent on the Pi.
def test_main_imports_cloud_client_and_sync_daemon() -> None:
    assert hasattr(kiosk_main, "CloudClient")
    assert hasattr(kiosk_main, "SyncDaemon")


# Verifies the boot path constructs both halves and schedules the
# daemon's run() on the loop. We can't exec() main() in a unit-test
# process (no qasync, no QApplication, no SQLCipher), but we can
# scan the source for the required wiring shape — that's enough to
# catch the "wiring deleted" regression that prompted this test.
def test_main_wires_sync_daemon_construction_and_loop_task() -> None:
    src = inspect.getsource(kiosk_main.main)
    # CloudClient constructed with the three settings keys.
    assert "CloudClient(" in src
    assert "base_url=settings.CLOUD_API_URL" in src
    assert "api_key=settings.KIOSK_API_KEY" in src
    assert "device_id=settings.KIOSK_DEVICE_ID" in src
    # SyncDaemon constructed with the existing session_factory and
    # the just-built cloud client.
    assert "SyncDaemon(" in src
    assert "session_factory=session_factory" in src
    assert "cloud=cloud_client" in src
    # run() scheduled on the qasync loop.
    assert "loop.create_task(sync_daemon.run())" in src
    # Boot log so journalctl shows the daemon started.
    assert "kiosk.boot.sync_daemon_started" in src
    # Done-callback so a daemon crash leaves a journalctl trace
    # instead of a silent failure.
    assert "add_done_callback" in src
    assert "kiosk.sync_daemon_crashed" in src


# Verifies the sync-task scheduling lives AFTER boot_sensors() so
# the daemon doesn't race the BLE/MQTT startup logging in journalctl.
# Mortality: would fail if a refactor reordered the boot tasks and
# made the kiosk's startup output less debuggable.
def test_main_starts_sync_after_boot_sensors() -> None:
    src = inspect.getsource(kiosk_main.main)
    boot_pos = src.index("loop.create_task(boot_sensors())")
    sync_pos = src.index("loop.create_task(sync_daemon.run())")
    assert boot_pos < sync_pos
