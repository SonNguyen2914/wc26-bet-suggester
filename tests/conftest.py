"""Test-environment defaults.

PUBLIC_READ_ONLY fails CLOSED in config (an absent env var means the
public API is read-only — the safe production posture). Tests are the
development environment: they opt out explicitly, exactly as a dev
deployment sets PUBLIC_READ_ONLY=false. Lockdown tests re-enable it
per-test via monkeypatch.
"""
import pytest

import config


@pytest.fixture(autouse=True)
def _dev_mode_defaults(monkeypatch):
    monkeypatch.setattr(config, "PUBLIC_READ_ONLY", False)
