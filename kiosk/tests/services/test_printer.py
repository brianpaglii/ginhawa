"""Printer service contract.

Phase 2 prompt 5 only locks down the PrintedStatus enum; the
python-escpos wiring lands in a later prompt. These smoke tests pin
the enum's string values so the schema-side ``printed_status`` CHECK
constraint cannot drift apart from the kiosk's enum without a test
failure.
"""

from __future__ import annotations

from ginhawa_kiosk.services.printer import PrintedStatus


# Verifies PrintedStatus exposes the five values the schema's CHECK
# constraint allows, and that each member's string form matches
# verbatim. The schema source-of-truth lives in /schema.sql; this
# test is the kiosk-side mirror.
# Mortality: would fail if any enum value were renamed (which would
# silently send a CHECK-violating string to the cloud's sync
# endpoint) or if a member were added without a corresponding
# schema migration.
def test_printed_status_values_match_schema() -> None:
    assert {s.value for s in PrintedStatus} == {
        "not_requested",
        "printed_ok",
        "paper_out_pre",
        "paper_out_mid",
        "print_failed",
    }
    # Members are StrEnum so direct equality with the raw string holds.
    assert PrintedStatus.NOT_REQUESTED == "not_requested"
    assert PrintedStatus.PRINTED_OK == "printed_ok"
