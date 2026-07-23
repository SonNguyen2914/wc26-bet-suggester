"""The LIVE data plane (MLS shadow mode, launch decision Jul 23).

Strictly separate from the WC26 archive plane: its own PostgreSQL
database (LIVE_DATABASE_URL), its own Alembic migration lineage, its
own models. Absent LIVE_DATABASE_URL the entire plane is dormant —
no engine, no writes, shadow endpoints report not-ready.
"""
