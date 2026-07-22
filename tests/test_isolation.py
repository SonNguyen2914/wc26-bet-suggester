"""The suite must never touch data/suggester.db (V7 evaluation F11)."""


def test_suite_uses_isolated_database():
    import config
    from src.db import engine
    assert "wc26-test-db-" in str(engine.url)
    assert "wc26-test-db-" in config.DATABASE_URL
    assert "data/suggester.db" not in str(engine.url)
