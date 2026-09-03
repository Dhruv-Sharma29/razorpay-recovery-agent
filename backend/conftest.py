"""Pytest configuration.

Force an in-memory audit database for the test session so tests never touch
the configured (persistent) database or leave a stray ``recovery.db`` behind.

Environment variables take precedence over the ``.env`` file in
pydantic-settings, so setting this before ``app.config.Settings()`` is first
instantiated (i.e. at conftest import, before any test module) makes the whole
suite hermetic. An explicit ``DATABASE_URL`` in the environment still wins.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
