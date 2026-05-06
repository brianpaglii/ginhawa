"""Bilingual string catalogue for the GUI.

Same shape as the printer service's ``_STRINGS`` table — every
user-facing string lives in one keyed structure indexed by
``Language``. Adding a third language is one new ``_GuiStrings``
instance at the bottom of this file plus a key in ``_STRINGS``;
nothing else in the GUI module needs to change.

CLAUDE.md: receipts (and screens) print in the language selected at
session start. The IDLE / END / ABORTED / ERROR screens are the one
exception — they display in BOTH languages simultaneously because
the citizen has not yet chosen a language at IDLE, and at the
terminal screens the language context is being torn down. Those
strings live in :data:`BILINGUAL_STRINGS`.

The module is import-only — it has no runtime side effects beyond
defining the catalogue, so importing it from a non-GUI test (for
asserting label text) is safe.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..fsm import Language


@dataclass(frozen=True)
class _GuiStrings:
    """All user-facing strings for one language."""

    # Common chrome
    cancel_button: str
    change_language_button: str
    submit_button: str
    back_button: str

    # IdleScreen — shown ONCE in each language via BILINGUAL_STRINGS,
    # but the per-language entries are also kept here for future
    # one-language splash variants.
    idle_tap_prompt: str

    # IdentifyingScreen
    identifying_title: str

    # LanguageSelectScreen
    language_select_title: str
    language_select_english: str
    language_select_tagalog: str

    # RegisterFormScreen
    register_title: str
    register_intro: str
    register_label_name: str
    register_label_dob: str
    register_label_sex: str
    register_label_sex_male: str
    register_label_sex_female: str
    register_label_sex_other: str
    register_label_barangay: str
    register_label_phone: str
    register_phone_optional: str
    register_validation_name_required: str
    register_validation_dob_required: str
    register_validation_sex_required: str
    register_validation_barangay_required: str

    # ConsentScreen
    consent_title: str
    consent_body: str
    consent_agree_button: str
    consent_disagree_button: str

    # PathChoiceScreen
    path_choice_title: str
    path_choice_vitals: str
    path_choice_anthropometric: str
    path_choice_full: str
    path_choice_help: str

    # MeasuringVitalsScreen
    measuring_vitals_title: str
    measuring_vitals_bp_instruction: str
    measuring_vitals_pulse_instruction: str
    measuring_vitals_capturing: str
    measuring_vitals_connect_button: str
    measuring_vitals_connect_help: str
    measuring_vitals_connecting: str

    # MeasuringAnthroScreen
    measuring_anthro_title: str
    measuring_anthro_height_instruction: str
    measuring_anthro_weight_instruction: str
    measuring_anthro_temperature_instruction: str
    measuring_anthro_capturing: str

    # ReportScreen
    report_title: str
    report_print_button: str
    report_finish_without_printing_button: str
    report_no_measurements: str
    report_printer_unavailable: str

    # PrintingScreen
    printing_title: str

    # EndScreen — both-languages screen via BILINGUAL_STRINGS, but
    # per-language closing also held here in case a single-language
    # END is ever wanted.
    end_thank_you: str
    end_auto_return_in: str  # template, e.g., "Returning in {n} seconds"

    # AbortedScreen
    aborted_title: str
    aborted_message: str

    # ErrorScreen
    error_title: str
    error_message: str
    error_diagnostic_label: str


_EN = _GuiStrings(
    cancel_button="Cancel",
    change_language_button="Change language",
    submit_button="Submit",
    back_button="Back",
    idle_tap_prompt="Tap your card to begin",
    identifying_title="Identifying...",
    language_select_title="Choose a language",
    language_select_english="English",
    language_select_tagalog="Tagalog",
    register_title="New citizen registration",
    register_intro=(
        "Please fill in your details. Your information is stored locally "
        "and synced securely under the Data Privacy Act of 2012."
    ),
    register_label_name="Full name",
    register_label_dob="Date of birth",
    register_label_sex="Sex",
    register_label_sex_male="Male",
    register_label_sex_female="Female",
    register_label_sex_other="Other",
    register_label_barangay="Barangay",
    register_label_phone="Phone",
    register_phone_optional="(optional)",
    register_validation_name_required="Please enter your full name.",
    register_validation_dob_required="Please choose your date of birth.",
    register_validation_sex_required="Please select an option.",
    register_validation_barangay_required="Please enter your barangay.",
    consent_title="Privacy notice",
    consent_body=(
        "GINHAWA records your health measurements to share with the Barangay "
        "Health Worker for follow-up care. Your information is encrypted on "
        "this kiosk and synced privately to the cloud. You may withdraw "
        "consent at any time. By tapping 'I agree' you confirm you have "
        "read and understood this notice."
    ),
    consent_agree_button="I agree",
    consent_disagree_button="I do not agree",
    path_choice_title="What would you like to measure?",
    path_choice_vitals="Vitals (BP, pulse, oxygen, temperature)",
    path_choice_anthropometric="Body measurements (height, weight)",
    path_choice_full="Full check (vitals + body measurements)",
    path_choice_help="Tap one option to begin.",
    measuring_vitals_title="Vitals",
    measuring_vitals_bp_instruction=(
        "1. Place the cuff on your upper arm.\n"
        "2. Press the START button on the cuff and wait for it to finish.\n"
        "3. Press the Bluetooth (BT) button on the cuff — the BT icon "
        "will flash.\n"
        "4. Tap 'Connect to cuff' below."
    ),
    measuring_vitals_pulse_instruction=(
        "Place your index finger inside the pulse oximeter cup. Stay still "
        "until the reading appears."
    ),
    measuring_vitals_capturing="Capturing... please wait.",
    measuring_vitals_connect_button="Connect to cuff",
    measuring_vitals_connect_help=(
        "Tap this AFTER you have pressed the BT button on the cuff and "
        "see the BT icon flashing."
    ),
    measuring_vitals_connecting="Connecting to cuff...",
    measuring_anthro_title="Body measurements",
    measuring_anthro_height_instruction=(
        "Stand straight under the height sensor. Look forward and stay still."
    ),
    measuring_anthro_weight_instruction=(
        "Step on the scale and stand still until the reading appears."
    ),
    measuring_anthro_temperature_instruction=(
        "Stand close to the thermal sensor with your forehead facing it."
    ),
    measuring_anthro_capturing="Capturing... please wait.",
    report_title="Your measurements",
    report_print_button="Print receipt",
    report_finish_without_printing_button="Finish without printing",
    report_no_measurements=(
        "No measurements were captured. Please consult the Barangay Health Worker."
    ),
    report_printer_unavailable=(
        "Printer is not available. You can finish without printing."
    ),
    printing_title="Printing your receipt...",
    end_thank_you="Thank you for your visit.",
    end_auto_return_in="Returning to start in {n} seconds.",
    aborted_title="Session cancelled",
    aborted_message="Your session has been cancelled. No measurements were saved.",
    error_title="Something went wrong",
    error_message=(
        "The kiosk could not finish your session. Please try again, or "
        "consult the Barangay Health Worker."
    ),
    error_diagnostic_label="Diagnostic code",
)


_TL = _GuiStrings(
    cancel_button="Kanselahin",
    change_language_button="Palitan ang wika",
    submit_button="Ipasa",
    back_button="Bumalik",
    idle_tap_prompt="I-tap ang card para magsimula",
    identifying_title="Hinahanap...",
    language_select_title="Pumili ng wika",
    language_select_english="English",
    language_select_tagalog="Tagalog",
    register_title="Pagpaparehistro ng bagong mamamayan",
    register_intro=(
        "Pakipuno ang iyong mga detalye. Ang iyong impormasyon ay "
        "iniingatan sa kiosk at i-sa-sync ng ligtas alinsunod sa Data "
        "Privacy Act of 2012."
    ),
    register_label_name="Buong pangalan",
    register_label_dob="Petsa ng kapanganakan",
    register_label_sex="Kasarian",
    register_label_sex_male="Lalaki",
    register_label_sex_female="Babae",
    register_label_sex_other="Iba pa",
    register_label_barangay="Barangay",
    register_label_phone="Numero ng telepono",
    register_phone_optional="(hindi kinakailangan)",
    register_validation_name_required="Pakilagay ang iyong buong pangalan.",
    register_validation_dob_required="Pakipili ang iyong petsa ng kapanganakan.",
    register_validation_sex_required="Pakipili ng isa.",
    register_validation_barangay_required="Pakilagay ang iyong barangay.",
    consent_title="Pahayag tungkol sa privacy",
    consent_body=(
        "Ang GINHAWA ay nag-iingat ng iyong mga sukat para maibahagi sa "
        "Barangay Health Worker para sa karagdagang pag-aalaga. Ang iyong "
        "impormasyon ay naka-encrypt sa kiosk na ito at ligtas na "
        "naka-sync sa cloud. Maaari mong bawiin ang pagsang-ayon kahit "
        "kailan. Sa pag-tap sa 'Sumasang-ayon ako' kinikilala mo na "
        "binasa at naunawaan mo ang pahayag na ito."
    ),
    consent_agree_button="Sumasang-ayon ako",
    consent_disagree_button="Hindi ako sumasang-ayon",
    path_choice_title="Ano ang gusto mong sukatin?",
    path_choice_vitals="Vital signs (BP, tibok, oxygen, temperatura)",
    path_choice_anthropometric="Sukat ng katawan (taas, timbang)",
    path_choice_full="Buong tsek (vital signs + sukat ng katawan)",
    path_choice_help="I-tap ang isa para magsimula.",
    measuring_vitals_title="Vital signs",
    measuring_vitals_bp_instruction=(
        "1. Ilagay ang cuff sa iyong braso.\n"
        "2. Pindutin ang START button sa cuff at hintayin matapos.\n"
        "3. Pindutin ang Bluetooth (BT) button sa cuff — kumukurap "
        "ang BT icon.\n"
        "4. I-tap ang 'Ikonekta sa cuff' sa ibaba."
    ),
    measuring_vitals_pulse_instruction=(
        "Ilagay ang iyong hintuturo sa loob ng pulse oximeter. Manatiling "
        "nakatigil hanggang lumitaw ang sukat."
    ),
    measuring_vitals_capturing="Sinusukat... pakihintay.",
    measuring_vitals_connect_button="Ikonekta sa cuff",
    measuring_vitals_connect_help=(
        "I-tap ito MATAPOS mong pindutin ang BT button sa cuff at "
        "kumukurap ang BT icon."
    ),
    measuring_vitals_connecting="Kumokonekta sa cuff...",
    measuring_anthro_title="Sukat ng katawan",
    measuring_anthro_height_instruction=(
        "Tumayo nang tuwid sa ilalim ng height sensor. Tumingin sa "
        "harap at manatiling nakatigil."
    ),
    measuring_anthro_weight_instruction=(
        "Tumayo sa timbangan at manatiling nakatigil hanggang lumitaw ang sukat."
    ),
    measuring_anthro_temperature_instruction=(
        "Tumayo malapit sa thermal sensor na nakaharap ang iyong noo."
    ),
    measuring_anthro_capturing="Sinusukat... pakihintay.",
    report_title="Ang iyong mga sukat",
    report_print_button="I-print ang resibo",
    report_finish_without_printing_button="Tapusin nang walang print",
    report_no_measurements=(
        "Walang nakuhang sukat. Mangyaring sumangguni sa Barangay Health Worker."
    ),
    report_printer_unavailable=(
        "Hindi gumagana ang printer. Maaari kang tapusin nang walang print."
    ),
    printing_title="Pina-print ang iyong resibo...",
    end_thank_you="Maraming salamat sa pagbisita.",
    end_auto_return_in="Babalik sa simula sa loob ng {n} segundo.",
    aborted_title="Kinansela ang sesyon",
    aborted_message="Kinansela ang iyong sesyon. Walang nai-save na sukat.",
    error_title="May naganap na problema",
    error_message=(
        "Hindi natapos ng kiosk ang iyong sesyon. Subukan muli, o "
        "sumangguni sa Barangay Health Worker."
    ),
    error_diagnostic_label="Diagnostic code",
)


_STRINGS: dict[Language, _GuiStrings] = {
    "en": _EN,
    "tl": _TL,
}


def get_strings(language: Language) -> _GuiStrings:
    """Look up the string catalogue for a language."""
    return _STRINGS[language]


# Bilingual block for IDLE / END / ABORTED / ERROR — shown in both
# languages on the same screen because no language has been chosen
# (IDLE) or the language context is being torn down (terminals).
@dataclass(frozen=True)
class _BilingualStrings:
    idle_tap_prompt_en: str
    idle_tap_prompt_tl: str
    end_thank_you_en: str
    end_thank_you_tl: str
    aborted_title_en: str
    aborted_title_tl: str
    aborted_message_en: str
    aborted_message_tl: str
    error_title_en: str
    error_title_tl: str
    error_message_en: str
    error_message_tl: str


BILINGUAL_STRINGS = _BilingualStrings(
    idle_tap_prompt_en=_EN.idle_tap_prompt,
    idle_tap_prompt_tl=_TL.idle_tap_prompt,
    end_thank_you_en=_EN.end_thank_you,
    end_thank_you_tl=_TL.end_thank_you,
    aborted_title_en=_EN.aborted_title,
    aborted_title_tl=_TL.aborted_title,
    aborted_message_en=_EN.aborted_message,
    aborted_message_tl=_TL.aborted_message,
    error_title_en=_EN.error_title,
    error_title_tl=_TL.error_title,
    error_message_en=_EN.error_message,
    error_message_tl=_TL.error_message,
)


__all__ = ["BILINGUAL_STRINGS", "Language", "_GuiStrings", "get_strings"]
