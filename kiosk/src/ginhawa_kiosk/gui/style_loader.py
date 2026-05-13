"""Build the kiosk's stylesheet from QSS template + design tokens."""

from __future__ import annotations
from importlib import resources

from .theme import PALETTE, SIZING, TYPOGRAPHY


def build_stylesheet() -> str:
    template = resources.read_text("ginhawa_kiosk.gui.resources", "styles.qss")
    return template.format(
        primary=PALETTE.primary,
        primary_dark=PALETTE.primary_dark,
        primary_light=PALETTE.primary_light,
        background=PALETTE.background,
        surface=PALETTE.surface,
        text_primary=PALETTE.text_primary,
        text_secondary=PALETTE.text_secondary,
        text_on_primary=PALETTE.text_on_primary,
        success=PALETTE.success,
        warning=PALETTE.warning,
        border=PALETTE.border,
        overlay=PALETTE.overlay,
        font_family=TYPOGRAPHY.family,
        h1_size=TYPOGRAPHY.h1,
        h2_size=TYPOGRAPHY.h2,
        h3_size=TYPOGRAPHY.h3,
        body_lg_size=TYPOGRAPHY.body_lg,
        body_size=TYPOGRAPHY.body,
        body_sm_size=TYPOGRAPHY.body_sm,
        button_lg_size=TYPOGRAPHY.button_lg,
        button_size=TYPOGRAPHY.button,
        measurement_value_size=TYPOGRAPHY.measurement_value,
        measurement_unit_size=TYPOGRAPHY.measurement_unit,
        measurement_label_size=TYPOGRAPHY.measurement_label,
        primary_button_min_width=SIZING.primary_button_min_width,
        primary_button_min_height=SIZING.primary_button_min_height,
        secondary_button_min_width=SIZING.secondary_button_min_width,
        secondary_button_min_height=SIZING.secondary_button_min_height,
        btn_radius=SIZING.button_border_radius,
        card_radius=SIZING.card_border_radius,
    )
