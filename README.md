# Aurum Pharma

SaaS-система автоматизации аптек для Республики Таджикистан. Мульти-тенантная, по подписке.

> Этап 1 (MVP). Активная разработка. Полные правила, стек и конвенции — в [CLAUDE.md](CLAUDE.md).

## Быстрый старт

```bash
cp .env.example .env
docker compose up -d
```

Применить миграции БД:

```bash
docker compose exec backend alembic upgrade head
```

Проверить локальное demo-состояние на Windows:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\demo-smoke.ps1
```

Подробно: [docs/local-demo.md](docs/local-demo.md).

После того как стек поднят:

- Backend API: <http://localhost:8000> (`/healthz`, `/docs`)
- Frontend: <http://localhost:5173>
- MinIO console: <http://localhost:9001>
- Prometheus: <http://localhost:9090>
- Postgres (основной): `localhost:5432`, БД `aurum`
- Postgres (тестовый): `localhost:5433`, БД `aurum_test`
- Redis: `localhost:6379`

## Production-периметр

Отдельный production Compose, non-root образы, Caddy/TLS и Docker secrets
описаны в [production deployment runbook](docs/runbooks/production-deployment.md).
Этот контур нельзя публиковать для пилота до закрытия оставшихся P0-блокеров из
[security release plan](docs/security-release-plan.md).

## Структура

```
aurum-pharma/
├── backend/            FastAPI + SQLAlchemy 2.0 async + Alembic
├── frontend/           React 18 + TS strict + Vite + TanStack
├── infra/              Postgres init, Prometheus config
├── scripts/            Локальные проверки и утилиты разработки
├── docs/               Спецификация, схема БД, план разработки
├── docker-compose.yml  Dev-окружение
└── CLAUDE.md           Правила и инструкции для AI-ассистента
```

## Документы

- [CLAUDE.md](CLAUDE.md) — правила, стек, архитектурные ограничения
- [docs/spec-v3.md](docs/spec-v3.md) — функциональная спецификация
- [docs/db-schema-v2.md](docs/db-schema-v2.md) — схема БД
- [docs/handoff.md](docs/handoff.md) — план разработки по доменам
- [docs/local-demo.md](docs/local-demo.md) — безопасная проверка локального demo
- [docs/known-issues.md](docs/known-issues.md) — известные проблемы

## Чек-лист после каждого домена

См. раздел 6 в [CLAUDE.md](CLAUDE.md).
