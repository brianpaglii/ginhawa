"""Theme + stylesheet loader smoke tests.

These tests don't need a QApplication — they exercise the pure
build_stylesheet function. Any regression in token wiring (missing
placeholder substitution, missing primary colour, empty render) is
visible immediately without booting Qt.
"""

from __future__ import annotations

import re

from ginhawa_kiosk.gui.style_loader import build_stylesheet


# Verifies the loader returns a non-empty stylesheet — a regression
# that breaks resource loading would surface as an empty string or
# an unhandled IOError. Mortality: would fail if the QSS resource
# went missing from the package or the loader returned "".
def test_build_stylesheet_returns_non_empty_string() -> None:
    css = build_stylesheet()
    assert isinstance(css, str)
    assert len(css) > 0


# Verifies the brand teal lands in the rendered stylesheet — pins
# the PALETTE.primary → {primary} placeholder pipeline. Mortality:
# would fail if the format() call dropped the primary key or the
# token name changed without updating the QSS.
def test_stylesheet_includes_primary_color() -> None:
    css = build_stylesheet()
    assert "#2A9D8F" in css


# Verifies every {placeholder} in the QSS template got substituted.
# A residual {primary_dark} or similar would imply the loader's
# token map drifted from the template. Mortality: would fail if a
# new placeholder were added to the template without a matching
# kwarg in build_stylesheet().
def test_stylesheet_substitutes_all_placeholders() -> None:
    css = build_stylesheet()
    # str.format already drops escaped {{ }} into single braces; any
    # leftover singular {token} means an unsubstituted placeholder.
    unresolved = re.findall(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}", css)
    assert unresolved == [], f"unresolved placeholders: {unresolved}"
