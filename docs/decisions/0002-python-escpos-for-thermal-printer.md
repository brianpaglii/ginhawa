# ADR 0002: python-escpos library for thermal printer driver

- **Status:** Accepted
- **Date:** 04-20-2026
- **Deciders:** Brian Paglinawan, Carl Justin Gasco, Lyan Libunao, Mariane Bersamira

## Context

The kiosk includes a Xprinter XP-58IIH thermal receipt printer for
giving citizens a printed copy of their measurement results. The
printer speaks the ESC/POS protocol over USB. The kiosk software needs
a Python library to drive it.

## Decision

Use **`python-escpos`** as the printer driver. It is the most widely
adopted Python library for ESC/POS printers, has active maintenance,
and supports the USB transport our printer uses.

## Alternatives considered

- _Roll our own ESC/POS implementation:_ rejected. ESC/POS has many
  vendor-specific quirks; using a maintained library that has already
  encountered them saves time and reduces bugs.
- _Use the printer manufacturer's proprietary SDK:_ rejected. Xprinter
  does not publish a Python SDK; their reference is for Windows C++.
  Reverse-engineering or wrapping is more work than `python-escpos`
  already does.
- _Print via a CUPS queue:_ rejected. CUPS adds a layer of complexity
  (a daemon to monitor, a queue to manage failures around) that the
  direct USB path avoids. The kiosk needs synchronous knowledge of
  print success/failure to update `printed_status` on the session.

## Consequences

- The printer service module wraps `python-escpos` rather than calling
  it directly from handlers, so a future library swap touches one file.
- Paper-out detection uses the library's status-query support; this is
  what the printer-integration design relies on.
- USB permissions on the Pi must allow the kiosk's user to access the
  printer's USB device node. The deployment runbook must include the
  udev rule.
- The library is single-process; concurrent print attempts must be
  serialized at the application layer.
