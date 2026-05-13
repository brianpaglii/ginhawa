"""Shared reusable widgets for the GINHAWA kiosk GUI.

These widgets carry the centralised theme via their ``objectName``
so the global stylesheet (built in :mod:`style_loader`) drives their
appearance. Each widget is kept thin: layout + objectName + a small
public API. Visual rules live in the QSS, not in inline
``setStyleSheet`` calls.
"""

from .branded_footer import BrandedFooter
from .branded_header import BrandedHeader
from .measurement_value import MeasurementValue
from .primary_button import PrimaryButton
from .secondary_button import SecondaryButton
from .section_card import SectionCard

__all__ = [
    "BrandedFooter",
    "BrandedHeader",
    "MeasurementValue",
    "PrimaryButton",
    "SecondaryButton",
    "SectionCard",
]
