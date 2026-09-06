"""Database engines must not render bound secrets in exceptions."""

from app.core.db import app_engine, support_engine
from app.core.system_worker_db import system_worker_engine


def test_database_engines_hide_bound_parameters() -> None:
    assert app_engine.sync_engine.hide_parameters is True
    assert support_engine.sync_engine.hide_parameters is True
    assert system_worker_engine.sync_engine.hide_parameters is True
