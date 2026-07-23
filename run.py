"""Run everything: DB init, background scheduler, and the API server.

    python run.py
"""
import uvicorn

import config
from src.db import init_db
from jobs.scheduler import start_scheduler


def main() -> None:
    init_db()
    # Live plane (MLS shadow): deliberate deploy-time migration + seed.
    # Raises on failure -> container exits -> the deploy fails visibly
    # instead of serving a half-migrated live database.
    from src.live.db import migrate_and_seed
    migrate_and_seed()
    scheduler = start_scheduler()
    print(f"Scheduler running: {[j.id for j in scheduler.get_jobs()]}")
    print(f"Demo mode: {config.DEMO_MODE}")
    uvicorn.run("api.main:app", host=config.API_HOST, port=config.API_PORT, log_level="info")


if __name__ == "__main__":
    main()
