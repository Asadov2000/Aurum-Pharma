"""Readiness endpoint status semantics."""

from __future__ import annotations

import json

import pytest

import app.main as main_module


class _Result:
    def scalar(self) -> int:
        return 1


class _Connection:
    async def execute(self, _statement: object) -> _Result:
        return _Result()


class _ConnectionContext:
    def __init__(self, *, fails: bool = False) -> None:
        self.fails = fails

    async def __aenter__(self) -> _Connection:
        if self.fails:
            raise ConnectionError("database unavailable")
        return _Connection()

    async def __aexit__(
        self,
        _exc_type: object,
        _exc: object,
        _traceback: object,
    ) -> None:
        return None


class _Engine:
    def __init__(self, *, fails: bool = False) -> None:
        self.fails = fails

    def connect(self) -> _ConnectionContext:
        return _ConnectionContext(fails=self.fails)


class _Redis:
    def __init__(self, *, fails: bool = False) -> None:
        self.fails = fails

    async def ping(self) -> bool:
        if self.fails:
            raise ConnectionError("redis unavailable")
        return True


@pytest.mark.parametrize(
    ("db_fails", "redis_fails"),
    [(True, False), (False, True), (True, True)],
)
async def test_healthz_returns_503_when_dependency_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    db_fails: bool,
    redis_fails: bool,
) -> None:
    monkeypatch.setattr(main_module, "app_engine", _Engine(fails=db_fails))
    monkeypatch.setattr(main_module, "redis_client", _Redis(fails=redis_fails))

    response = await main_module.healthz()

    assert response.status_code == 503
    assert json.loads(response.body) == {
        "status": "degraded",
        "db": not db_fails,
        "redis": not redis_fails,
    }


async def test_healthz_returns_200_when_dependencies_are_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main_module, "app_engine", _Engine())
    monkeypatch.setattr(main_module, "redis_client", _Redis())

    response = await main_module.healthz()

    assert response.status_code == 200
    assert json.loads(response.body) == {
        "status": "ok",
        "db": True,
        "redis": True,
    }
