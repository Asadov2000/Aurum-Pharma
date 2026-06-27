# Локальная демо-проверка

Этот документ описывает безопасный способ проверить, что локальная демо-среда
Aurum Pharma готова к показу и разработке.

## Быстрая проверка

Из корня проекта на Windows:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\demo-smoke.ps1
```

Скрипт делает только безопасные действия:

- поднимает Docker Compose стек;
- показывает статус контейнеров;
- проверяет healthcheck у Postgres, test-Postgres, Redis и MinIO;
- запускает `alembic upgrade head` (миграции вверх, без `downgrade`);
- проверяет `http://localhost:8000/healthz`, `http://localhost:8000/docs` и frontend;
- проверяет наличие demo-данных через read-only SQL-запросы.

Скрипт не делает:

- `DROP DATABASE`, `TRUNCATE`, `DELETE` по бизнес-таблицам;
- `alembic downgrade`;
- `SEED_DEMO_FORCE=1`;
- ручное пересоздание `dev@aurum.tj`, `admin@aurum.tj`, `owner@aurum.tj`.

## Если demo-данные каталога пустые

Для наполнения каталога, остатков и истории продаж:

```powershell
docker compose exec backend python -m app.seed_demo
```

Этот seeder идемпотентный: если demo уже наполнено, повторный запуск ничего не
дублирует. Принудительный пересев через `SEED_DEMO_FORCE=1` считается отдельным
решением и не входит в smoke-проверку.

## Проверка с E2E

E2E (сквозные браузерные тесты) запускаются с хоста, не из контейнера frontend:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\demo-smoke.ps1 -RunE2E
```

Причина: `frontend/e2e/global-setup.ts` использует Docker CLI, а Playwright
браузеры должны запускаться на хосте. Скрипт запускает
`frontend/node_modules/.bin/playwright.cmd` напрямую, без автопереустановки
зависимостей перед тестами. Поэтому перед E2E на хосте должны быть установлены
зависимости frontend:

```powershell
cd frontend
pnpm install
pnpm exec playwright install chromium
```

## Что означает ошибка по базовым seed-аккаунтам

Если smoke-проверка пишет, что нет `dev@aurum.tj`, `admin@aurum.tj` или
`owner@aurum.tj`, не создавай их вручную SQL-запросами. Это базовый dev-seed,
который должен жить в общей dev-БД. В такой ситуации нужно остановиться и
восстановить ожидаемое состояние dev-БД из принятого seed-процесса проекта.
