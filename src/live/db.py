"""Live-plane database access. Dormant without LIVE_DATABASE_URL."""
from __future__ import annotations

import config

_engine = None
_Session = None


def live_enabled() -> bool:
    return bool(config.LIVE_DATABASE_URL)


def get_engine():
    """Lazy engine; never created while the plane is dormant."""
    global _engine
    if not live_enabled():
        return None
    if _engine is None:
        from sqlalchemy import create_engine
        _engine = create_engine(config.LIVE_DATABASE_URL,
                                pool_pre_ping=True, future=True)
    return _engine


def get_session():
    global _Session
    if _Session is None:
        eng = get_engine()
        if eng is None:
            return None
        from sqlalchemy.orm import sessionmaker
        _Session = sessionmaker(bind=eng, future=True)
    return _Session()


def migrations_current() -> bool | None:
    """True/False when the plane is active; None while dormant. The live
    service must FAIL STARTUP when migrations are behind — schema is
    never created or modified ad hoc during request handling."""
    eng = get_engine()
    if eng is None:
        return None
    from alembic.config import Config as AlembicConfig
    from alembic.script import ScriptDirectory
    from sqlalchemy import text
    import os
    cfg = AlembicConfig(os.path.join(os.path.dirname(__file__),
                                     "..", "..", "alembic.ini"))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    with eng.connect() as conn:
        try:
            row = conn.execute(
                text("SELECT version_num FROM alembic_version")).first()
        except Exception:
            return False                 # no version table = behind
    return bool(row and row[0] == head)


def startup_check() -> None:
    """Raise when the live plane is active but not migrated."""
    state = migrations_current()
    if state is False:
        raise RuntimeError(
            "live database migrations are behind — run "
            "`alembic upgrade head` before serving the live plane")
