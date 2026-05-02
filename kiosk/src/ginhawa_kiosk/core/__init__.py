"""Core wiring: settings, logging, SQLCipher key handling.

These modules have no project-internal dependencies and are imported
by every other subpackage. Anything that would create a cycle (e.g.,
importing from db/ here) is wrong by construction.
"""
