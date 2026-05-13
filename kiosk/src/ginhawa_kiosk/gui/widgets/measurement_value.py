"""Composite measurement display widget.

Renders one measurement (e.g., "Systolic BP — 128 mmHg") with a
small uppercase label, a large value, an inline muted unit, and a
small status badge ("OK" / "Sensor offline"). When the value is
unavailable (em-dash sentinel or ``valid=False``), the rendering
shifts to muted styling and surfaces an "invalid_note" badge.

All visual styling lives in the global stylesheet via objectNames
on the underlying labels.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget


_EMDASH = "—"


class MeasurementValue(QWidget):
    """Composite widget for one measurement row.

    The widget owns its layout (label above, value+unit+badge below).
    The visual treatment is determined by ``valid`` and the literal
    em-dash sentinel (``"—"``), which is also treated as missing.
    """

    def __init__(
        self,
        label: str,
        value: str,
        unit: str,
        *,
        valid: bool = True,
        invalid_note: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("measurementValue_root")

        is_missing = value == _EMDASH
        is_invalid = (not valid) or is_missing

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        self._label = QLabel(label.upper())
        self._label.setObjectName("measurementLabel")
        outer.addWidget(self._label)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.setAlignment(Qt.AlignmentFlag.AlignLeft)

        self._value = QLabel(value)
        self._value.setObjectName(
            "measurementValueInvalid" if is_invalid else "measurementValue"
        )
        row.addWidget(self._value)

        self._unit = QLabel(unit)
        self._unit.setObjectName("measurementUnit")
        row.addWidget(self._unit)

        self._badge = QLabel("")
        if is_invalid and invalid_note:
            self._badge.setText(invalid_note)
            self._badge.setObjectName("statusBadgeInvalid")
        else:
            self._badge.setObjectName("statusBadgeValid")
            self._badge.setText("OK" if valid and not is_missing else "")
            if not (valid and not is_missing):
                self._badge.setVisible(False)
        row.addWidget(self._badge)
        row.addStretch(1)

        outer.addLayout(row)
