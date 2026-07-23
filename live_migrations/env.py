"""Alembic environment for the live plane. The URL comes from
config.LIVE_DATABASE_URL (or -x url=... for tests)."""
import os
import sys

from alembic import context
from sqlalchemy import create_engine

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config  # noqa: E402
from src.live.models import LiveBase  # noqa: E402

target_metadata = LiveBase.metadata


def _url() -> str:
    xargs = context.get_x_argument(as_dictionary=True)
    return xargs.get("url") or config.LIVE_DATABASE_URL


def run_migrations_offline():
    context.configure(url=_url(), target_metadata=target_metadata,
                      literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    url = _url()
    if not url:
        raise SystemExit("LIVE_DATABASE_URL not set (or pass -x url=...)")
    engine = create_engine(url, future=True)
    with engine.connect() as connection:
        context.configure(connection=connection,
                          target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
