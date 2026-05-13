"""Design tokens for the GINHAWA kiosk GUI."""

from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class _Palette:
    primary: str = "#2A9D8F"
    primary_dark: str = "#1F7A6F"
    primary_light: str = "#7AC4BA"
    background: str = "#FFFFFF"
    surface: str = "#F5F1E8"
    text_primary: str = "#1A2A2E"
    text_secondary: str = "#4A5859"
    text_on_primary: str = "#FFFFFF"
    success: str = "#52B788"
    warning: str = "#E76F51"
    border: str = "#D6D3CC"
    overlay: str = "rgba(26, 42, 46, 0.06)"


@dataclass(frozen=True)
class _Typography:
    family: str = '"Inter", "Sans Serif"'
    h1: int = 72
    h2: int = 48
    h3: int = 36
    body_lg: int = 32
    body: int = 24
    body_sm: int = 20
    button_lg: int = 36
    button: int = 28
    measurement_value: int = 72
    measurement_unit: int = 28
    measurement_label: int = 22


@dataclass(frozen=True)
class _Spacing:
    xs: int = 8
    sm: int = 16
    md: int = 24
    lg: int = 40
    xl: int = 64
    xxl: int = 96


@dataclass(frozen=True)
class _Sizing:
    header_height: int = 120
    footer_height: int = 60
    primary_button_min_width: int = 320
    primary_button_min_height: int = 120
    secondary_button_min_width: int = 240
    secondary_button_min_height: int = 80
    card_padding: int = 32
    card_border_radius: int = 16
    button_border_radius: int = 12


PALETTE = _Palette()
TYPOGRAPHY = _Typography()
SPACING = _Spacing()
SIZING = _Sizing()
