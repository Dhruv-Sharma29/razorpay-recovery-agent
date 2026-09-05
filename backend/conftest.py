"""Pytest configuration.

Environment variables take precedence over the ``.env`` file in
pydantic-settings, so anything set here — before ``app.config.Settings()`` is
first instantiated, i.e. at conftest import and ahead of any test module —
applies to the whole suite.

Two things are forced:

1. **An in-memory audit database**, so tests never touch the configured
   persistent one or leave a stray ``recovery.db`` behind.
2. **No NIM API key**, so no test makes a live model call. A developer with a
   real key in ``.env`` would otherwise turn a six-second suite into minutes
   of rate-limited network traffic whose results depend on someone else's
   uptime. Both engines fall back deterministically on an empty key, which is
   what almost every test wants anyway.

Set ``REFLOW_TEST_LIVE_NIM=1`` to opt back in when deliberately exercising the
live path. An explicit ``DATABASE_URL`` in the environment still wins.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

if os.environ.get("REFLOW_TEST_LIVE_NIM") != "1":
    # Assigned, not setdefault: the point is to override whatever .env holds.
    os.environ["NIM_API_KEY"] = ""

# Tests that simulate a NIM outage would otherwise sleep through real backoff,
# which is wall-clock spent proving nothing. Production keeps its retries.
os.environ.setdefault("NIM_MAX_RETRIES", "0")
