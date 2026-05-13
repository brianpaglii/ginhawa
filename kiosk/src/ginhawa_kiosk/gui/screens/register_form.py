"""REGISTER_FORM: self-service citizen registration.

Collects name, DOB, sex, barangay, optional phone. Validation is
inline — empty required fields produce a banner; the screen does
NOT emit ``submitted`` until validation passes.

The barangay field is pre-filled from
``device_config.deployment_barangay`` (passed in via
:class:`RegisterFormScreen` constructor) so the kiosk's location is
the default and the citizen only edits if they're visiting from a
different barangay. Phone is optional.

On a successful submit the screen emits :attr:`submitted` with a
:class:`RegistrationData` dataclass; the main window inserts the
Citizen row, calls ``fsm.set_current_citizen`` + ``fsm.registration_complete()``.

The screen resets its inputs on every ``on_enter`` so a "Change
language" round-trip lands on a clean form (the test
``test_change_language_resets_partial_register_form_data`` pins
this).
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QDate, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QDateEdit,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
)

from ..strings import Language, get_strings
from .base import BaseScreen


@dataclass(frozen=True)
class RegistrationData:
    full_name: str
    dob_iso: str
    sex: str  # 'M' | 'F' | 'O'
    barangay: str
    phone: str | None


class RegisterFormScreen(BaseScreen):
    submitted = pyqtSignal(object)  # RegistrationData

    def __init__(self, *, default_barangay: str = "") -> None:
        super().__init__()
        self.setObjectName("register_form_screen")
        self._default_barangay = default_barangay

        # Heading + intro
        self._title = QLabel()
        self._title.setObjectName("register_title")
        self._intro = QLabel()
        self._intro.setObjectName("register_intro")
        self._intro.setWordWrap(True)

        # Form fields
        self._name_input = QLineEdit()
        self._name_input.setObjectName("register_name_input")
        self._dob_input = QDateEdit()
        self._dob_input.setObjectName("register_dob_input")
        self._dob_input.setCalendarPopup(True)
        self._dob_input.setDisplayFormat("yyyy-MM-dd")
        self._dob_input.setDate(QDate(2000, 1, 1))
        self._barangay_input = QLineEdit()
        self._barangay_input.setObjectName("register_barangay_input")
        self._phone_input = QLineEdit()
        self._phone_input.setObjectName("register_phone_input")

        # Sex radios
        self._sex_male = QRadioButton()
        self._sex_male.setObjectName("register_sex_male")
        self._sex_female = QRadioButton()
        self._sex_female.setObjectName("register_sex_female")
        self._sex_other = QRadioButton()
        self._sex_other.setObjectName("register_sex_other")
        self._sex_group = QButtonGroup(self)
        self._sex_group.addButton(self._sex_male, 0)
        self._sex_group.addButton(self._sex_female, 1)
        self._sex_group.addButton(self._sex_other, 2)

        # Validation banner
        self._error_label = QLabel()
        self._error_label.setObjectName("register_error_label")
        self._error_label.setWordWrap(True)
        self._error_label.setVisible(False)

        # Submit button
        self._submit_button = QPushButton()
        self._submit_button.setObjectName("register_submit_button")
        self._submit_button.clicked.connect(self._on_submit_clicked)

        # Field labels — populated in on_enter() so they re-render on
        # language change.
        self._name_label = QLabel()
        self._dob_label = QLabel()
        self._sex_label = QLabel()
        self._barangay_label = QLabel()
        self._phone_label = QLabel()

        # Form layout
        form = QFormLayout()
        form.addRow(self._name_label, self._name_input)
        form.addRow(self._dob_label, self._dob_input)

        sex_row = QHBoxLayout()
        sex_row.addWidget(self._sex_male)
        sex_row.addWidget(self._sex_female)
        sex_row.addWidget(self._sex_other)
        sex_row.addStretch(1)
        form.addRow(self._sex_label, sex_row)

        form.addRow(self._barangay_label, self._barangay_input)
        form.addRow(self._phone_label, self._phone_input)

        layout = QVBoxLayout()
        layout.addWidget(self._title)
        layout.addWidget(self._intro)
        layout.addSpacing(20)
        layout.addLayout(form)
        layout.addWidget(self._error_label)
        layout.addStretch(1)
        layout.addWidget(self._submit_button, alignment=Qt.AlignmentFlag.AlignRight)
        layout.addLayout(self._build_chrome_row())

        self.setLayout(layout)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_enter(self, language: Language) -> None:
        super().on_enter(language)
        s = get_strings(language)
        self._title.setText(s.register_title)
        self._intro.setText(s.register_intro)
        self._name_label.setText(s.register_label_name)
        self._dob_label.setText(s.register_label_dob)
        self._sex_label.setText(s.register_label_sex)
        self._sex_male.setText(s.register_label_sex_male)
        self._sex_female.setText(s.register_label_sex_female)
        self._sex_other.setText(s.register_label_sex_other)
        self._barangay_label.setText(s.register_label_barangay)
        self._phone_label.setText(
            f"{s.register_label_phone} {s.register_phone_optional}"
        )
        self._submit_button.setText(s.submit_button)
        self._reset_inputs()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _reset_inputs(self) -> None:
        self._name_input.clear()
        self._dob_input.setDate(QDate(2000, 1, 1))
        self._barangay_input.setText(self._default_barangay)
        self._phone_input.clear()
        self._sex_group.setExclusive(False)
        for btn in (self._sex_male, self._sex_female, self._sex_other):
            btn.setChecked(False)
        self._sex_group.setExclusive(True)
        self._error_label.setVisible(False)
        self._error_label.clear()

    def _selected_sex(self) -> str | None:
        if self._sex_male.isChecked():
            return "M"
        if self._sex_female.isChecked():
            return "F"
        if self._sex_other.isChecked():
            return "O"
        return None

    def _on_submit_clicked(self) -> None:
        # `on_enter` set self._language; validation errors render in
        # the active language without re-threading it through.
        s = get_strings(self._language)
        errors: list[str] = []
        name = self._name_input.text().strip()
        if not name:
            errors.append(s.register_validation_name_required)
        sex = self._selected_sex()
        if sex is None:
            errors.append(s.register_validation_sex_required)
        barangay = self._barangay_input.text().strip()
        if not barangay:
            errors.append(s.register_validation_barangay_required)

        if errors:
            self._error_label.setText("\n".join(errors))
            self._error_label.setVisible(True)
            return

        assert sex is not None  # narrowed above
        dob_iso = self._dob_input.date().toString("yyyy-MM-dd")
        phone_raw = self._phone_input.text().strip()
        data = RegistrationData(
            full_name=name,
            dob_iso=dob_iso,
            sex=sex,
            barangay=barangay,
            phone=phone_raw or None,
        )
        self._error_label.setVisible(False)
        self.submitted.emit(data)
