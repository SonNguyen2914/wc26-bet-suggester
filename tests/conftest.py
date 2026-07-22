"""Test-environment defaults.

DATABASE ISOLATION (V7 evaluation F11): the suite must NEVER touch the
developer's data/suggester.db. The engine binds to config.DATABASE_URL at
src.db import time, so the override happens HERE, before any application
import — conftest is the first project code pytest loads.

PUBLIC_READ_ONLY fails CLOSED in config (an absent env var means the
public API is read-only — the safe production posture). Tests are the
development environment: they opt out explicitly, exactly as a dev
deployment sets PUBLIC_READ_ONLY=false. Lockdown tests re-enable it
per-test via monkeypatch.
"""
import os
import tempfile

_TMPDIR = tempfile.mkdtemp(prefix="wc26-test-db-")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMPDIR}/test.db"

import pytest  # noqa: E402

import config  # noqa: E402  (reads the env var set above)

config.DATABASE_URL = os.environ["DATABASE_URL"]   # belt and braces


@pytest.fixture(autouse=True)
def _dev_mode_defaults(monkeypatch):
    monkeypatch.setattr(config, "PUBLIC_READ_ONLY", False)
