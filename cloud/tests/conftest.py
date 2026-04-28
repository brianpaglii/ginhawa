"""Test-wide setup.

Runs before any test module imports, so it can prime the env vars that
``ginhawa_cloud.core.config.Settings`` requires. Without this, importing
the application package would raise ValidationError when env vars and
``.env`` are absent.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("JWT_SECRET", "test-only-secret-not-for-production")
