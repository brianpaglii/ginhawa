"""Application services: audit, measurement validation, printer.

These modules consume the data layer (``db/``) but do not consume the
GUI or FSM layer. They are the only sanctioned writers to the
kiosk's local ``audit_log`` (mirroring the cloud's
``services.audit.record_audit`` pattern).
"""
