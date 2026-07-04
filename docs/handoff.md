# Aurum Pharma — Handoff: план разработки по доменам

> **Версия:** 1.0
> **Дата:** май 2026
> **Аудитория:** разработчик + VS Code Claude

Этот документ — рабочий план Этапа 1. На каждый из 12 доменов есть:
- **Что делать** (краткое описание)
- **Готовый промпт** для VS Code Claude (копируешь как есть)
- **Acceptance criteria** (чек-лист готовности)
- **Common pitfalls** (что обычно ломается)

---

## 0. Как пользоваться

### Workflow одного домена

```
1. Открываешь VS Code, репо синхронизирован с main
2. Открываешь чат с Claude for VS Code
3. Копируешь промпт нужного домена из этого файла
4. Claude пишет код, может задавать уточняющие вопросы
5. После реализации — Claude прогоняет чек-лист и коммитит сам
6. Ты проверяешь руками (smoke test) и переходишь к следующему домену
7. Если что-то непонятно — возвращаешься к Claude в чате
```

### Когда зовёшь меня (Claude в проектном чате)

- Промпт домена сработал → запрос на следующий домен или ревью
- Что-то падает → описание ошибки + что Claude уже пробовал
- Архитектурное сомнение → описание ситуации + предложение

### Жёсткие правила

1. **Один домен — один коммит** (или серия атомарных коммитов, но в рамках одного домена)
2. **Не двигаемся дальше, пока чек-лист не зелёный**
3. **Нет миграций «потом доделаем»** — миграция домена должна быть полной
4. **Каждый домен пишет минимум 3 теста** (happy / business rule / tenant isolation)
5. **Никаких `# TODO: исправить позже`** — либо делаем сейчас, либо явно фиксируем в `docs/known-issues.md`

---

## 1. Подготовка: репозиторий и инфраструктура

### 1.1 Промпт VS Code Claude — bootstrap репо

> Я начинаю проект Aurum Pharma — SaaS-систему для аптек Таджикистана. Создай скелет монорепозитория согласно `CLAUDE.md`, `docs/spec-v3.md` и `docs/db-schema-v2.md` (все три файла лежат в корне).
>
> **Структура:**
> ```
> aurum-pharma/
> ├── .github/workflows/ci.yml
> ├── .gitignore
> ├── .gitattributes               # LF для всех текстовых файлов
> ├── .editorconfig
> ├── CLAUDE.md                    # уже существует
> ├── README.md
> ├── docker-compose.yml           # postgres, postgres-test, redis, minio
> ├── .env.example
> ├── docs/
> │   ├── spec-v3.md               # уже существует
> │   ├── db-schema-v2.md          # уже существует
> │   ├── handoff.md               # уже существует
> │   └── known-issues.md          # пустой, заполняем по мере
> ├── infra/
> │   └── postgres/init.sql        # CREATE ROLE aurum_app, aurum_support
> ├── backend/
> │   ├── pyproject.toml
> │   ├── Dockerfile
> │   ├── .env.example -> ../.env.example
> │   ├── alembic.ini
> │   ├── alembic/
> │   │   ├── env.py
> │   │   └── versions/
> │   ├── app/
> │   │   ├── main.py
> │   │   ├── core/
> │   │   │   ├── __init__.py
> │   │   │   ├── config.py        # Pydantic Settings
> │   │   │   ├── db.py            # async engine, two pools, get_db dep
> │   │   │   ├── deps.py          # FastAPI dependencies
> │   │   │   ├── errors.py        # AurumError hierarchy
> │   │   │   ├── logging.py       # structlog config
> │   │   │   ├── redis.py         # async redis client
> │   │   │   ├── security.py      # JWT, hashing
> │   │   │   └── time.py          # utc_now()
> │   │   ├── middleware/
> │   │   │   ├── request_id.py
> │   │   │   ├── auth_context.py  # сетит app.tenant_id/user_id GUC
> │   │   │   └── error_handler.py
> │   │   ├── domains/             # пусто, заполняется в следующих доменах
> │   │   └── tasks/
> │   │       ├── celery_app.py
> │   │       └── __init__.py
> │   └── tests/
> │       ├── conftest.py          # fixtures: db_session с SAVEPOINT, client, redis
> │       ├── isolation/__init__.py
> │       └── domains/__init__.py
> └── frontend/
>     ├── package.json
>     ├── pnpm-lock.yaml
>     ├── tsconfig.json
>     ├── vite.config.ts
>     ├── tailwind.config.ts
>     ├── postcss.config.js
>     ├── index.html
>     ├── Dockerfile
>     ├── .eslintrc.cjs
>     ├── src/
>     │   ├── main.tsx
>     │   ├── routes/              # TanStack Router file-based
>     │   ├── components/ui/       # свои примитивы
>     │   ├── features/            # пусто, заполняется
>     │   ├── lib/
>     │   │   ├── api.ts           # axios + interceptors
>     │   │   ├── query.ts         # TanStack Query client
>     │   │   └── utils.ts
>     │   ├── stores/              # Zustand
>     │   └── styles/index.css
>     └── tests/
> ```
>
> **Технические требования:**
>
> 1. Стек строго по `CLAUDE.md`, версии библиотек тоже
> 2. `pyproject.toml` использует Poetry или PDM (на твой выбор) с зависимостями: fastapi, sqlalchemy[asyncio], asyncpg, alembic, pydantic, pydantic-settings, python-jose[cryptography], passlib[bcrypt], celery, redis, structlog, prometheus-client, httpx, factory-boy, pytest, pytest-asyncio, ruff, black, mypy
> 3. `docker-compose.yml` поднимает:
>    - `postgres` (PostgreSQL 16, основной)
>    - `postgres-test` (PostgreSQL 16, для тестов, разные порт и БД)
>    - `redis` (Redis 7)
>    - `minio` (с консолью на 9001)
>    - `backend` (со --reload, polling для Windows)
>    - `celery-worker`
>    - `celery-beat`
>    - `frontend` (vite dev server)
>    - `prometheus`
> 4. Named volumes для PostgreSQL (НЕ bind mount — медленно на Windows)
> 5. `infra/postgres/init.sql` создаёт две роли БД:
>    ```sql
>    CREATE ROLE aurum_app WITH LOGIN PASSWORD '${AURUM_APP_PASSWORD}';
>    CREATE ROLE aurum_support WITH LOGIN PASSWORD '${AURUM_SUPPORT_PASSWORD}' BYPASSRLS;
>    GRANT ALL ON DATABASE aurum TO aurum_app, aurum_support;
>    ```
> 6. `app/core/db.py` создаёт **два** engine: `app_engine` (через aurum_app) и `support_engine` (через aurum_support). Зависимость `get_db` выбирает engine на основе `CurrentUser.is_developer or is_administrator`
> 7. `app/core/config.py` — Pydantic Settings, читает `.env`, поля: `DATABASE_URL_APP`, `DATABASE_URL_SUPPORT`, `REDIS_URL`, `JWT_SECRET`, `JWT_ALGORITHM='HS256'`, `ACCESS_TOKEN_MINUTES=15`, `REFRESH_TOKEN_DAYS=7`, `CORS_ORIGINS` (список), `MINIO_ENDPOINT/KEY/SECRET`, `EMAIL_*` (smtp), `ENVIRONMENT='development'`
> 8. `app/core/errors.py` — иерархия `AurumError` (HTTP 500) → `NotFoundError` (404), `ValidationError` (422), `ConflictError` (409), `PermissionDeniedError` (403), `AuthenticationError` (401), `RateLimitError` (429), `BusinessRuleError` (422)
> 9. `app/middleware/error_handler.py` — глобальный обработчик: ловит `AurumError`, возвращает `{"error": {"code": ..., "message": ..., "details": ...}}`
> 10. `app/middleware/auth_context.py` — extractит JWT, ставит GUC `app.tenant_id`, `app.user_id`, `app.support_session` на сессии БД. Если JWT нет — GUC не ставит
> 11. `app/main.py` — собирает FastAPI app, добавляет middleware в правильном порядке (request_id → auth_context → error_handler), включает CORS правильно (явный список methods+headers, allow_credentials=True), `/healthz` проверяет БД и Redis
> 12. **CRITICAL CORS:** при `allow_credentials=True` НЕЛЬЗЯ `allow_methods=["*"]` или `allow_headers=["*"]`. Явно перечисли: methods = GET, POST, PATCH, DELETE, OPTIONS; headers = Authorization, Content-Type, X-Request-ID
> 13. `tests/conftest.py` — фикстура `db_session` использует SAVEPOINT (nested transaction), `rollback` в teardown — каждый тест полностью изолирован. `client` фикстура — `AsyncClient` с ASGITransport
> 14. `.gitignore` — `__pycache__`, `.venv`, `node_modules`, `dist`, `.env`, `*.pyc`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`
> 15. `.github/workflows/ci.yml` — на каждый push в main: ruff, black --check, mypy, pytest. Postgres контейнером
> 16. Никакой бизнес-логики в этом этапе — только инфраструктура и каркас
> 17. После всего — `docker compose up -d` поднимается без ошибок, `docker compose exec backend pytest tests/` проходит (тестов 0, ОК)
>
> **Не делай:**
> - Не создавай домены (auth, foundation, etc) — они будут отдельно
> - Не создавай первую миграцию — она будет в bootstrap-домене
> - Не пиши кейсы регистрации/логина — это в домене auth
>
> **После завершения — закоммить и запушь:**
> `git add . && git commit -m "chore: initial repo bootstrap" && git push`

### 1.2 Acceptance criteria

- [ ] `docker compose up -d` → все сервисы healthy
- [ ] `docker compose exec backend python -c "from app.main import app"` → без ошибок
- [ ] `docker compose exec backend pytest tests/` → "0 passed"
- [ ] `docker compose exec backend ruff check app` → clean
- [ ] `docker compose exec backend mypy app` → clean
- [ ] `docker compose exec backend curl http://localhost:8000/healthz` → 200 OK с проверкой БД и Redis
- [ ] `docker compose exec frontend pnpm typecheck` → clean
- [ ] `git log` → один коммит "chore: initial repo bootstrap"
- [ ] `git remote -v` → есть origin, push прошёл

### 1.3 Common pitfalls

| Проблема | Решение |
|---|---|
| CORS преflight failures в браузере | Не использовать `*` в methods/headers с `allow_credentials=True` |
| Очень медленный hot-reload на Windows | Polling: `uvicorn --reload --reload-include "*.py"` + переменная `WATCHFILES_FORCE_POLLING=true` |
| `asyncpg` ругается на `sslmode=...` в URL | Используй `?ssl=disable` (asyncpg) или вообще без ssl-параметра в локалке |
| Тесты падают на `Connection refused` к Postgres | Поднимай `postgres-test` отдельным контейнером с другим портом, проверяй healthcheck в compose |
| `monkey_patch` на `Base.__init_subclass__` ломает SQLAlchemy 2.0 | НЕ ДЕЛАЙ. Если нужна общая логика — наследуйся от `Base` напрямую или используй mixins |

---

## 2. Домен: bootstrap (миграция 0001)

### 2.1 Что делать

Первая миграция Alembic: расширения `pgcrypto`, `pg_trgm`, `unaccent` + функции `current_tenant_id()`, `current_app_user_id()`, `is_support_session()`, `trg_set_updated_meta()`, `trg_set_created_meta()`, `trg_audit_log()`.

### 2.2 Промпт

> Создай миграцию Alembic 0001 для проекта Aurum Pharma согласно `docs/db-schema-v2.md` раздел 2 «Расширения и базовые функции» и раздел 4 «Migration 0001 — extensions».
>
> **Конкретно:**
>
> 1. `alembic/versions/0001_extensions_and_helpers.py`
> 2. `upgrade()` создаёт:
>    - расширения `pgcrypto`, `pg_trgm`, `unaccent`
>    - функции: `current_tenant_id()`, `current_app_user_id()`, `is_support_session()`
>    - функции триггеров: `trg_set_updated_meta()`, `trg_set_created_meta()`, `trg_audit_log()`
> 3. `downgrade()` удаляет всё в обратном порядке
> 4. Используй `op.execute(""" ... """)` для DDL, без `sa.func.create_function`
> 5. Все SQL — точно как в схеме, не отсебятина
>
> **После реализации:**
> 1. `docker compose exec backend alembic upgrade head` → без ошибок
> 2. `docker compose exec backend alembic downgrade -1` → без ошибок
> 3. `docker compose exec backend alembic upgrade head` → без ошибок (идемпотентность)
> 4. Проверь руками:
>    ```sql
>    SELECT extname FROM pg_extension;
>    -- pgcrypto, pg_trgm, unaccent должны быть
>    SELECT proname FROM pg_proc WHERE proname LIKE 'current_%' OR proname LIKE 'trg_%';
>    -- 6 функций
>    ```
> 5. Закоммить: `git add . && git commit -m "feat(bootstrap): add migration 0001 extensions and helpers" && git push`

### 2.3 Acceptance criteria

- [ ] Миграция `0001` применяется и откатывается без ошибок
- [ ] В БД присутствуют 3 расширения и 6 функций
- [ ] `audit_log` ещё нет (это в миграции 0010) — `trg_audit_log()` создана, но не привязана к таблицам
- [ ] Коммит в `main`, тест из CI прошёл

---

## 3. Домен: auth (миграция 0002 + код)

### 3.1 Что делать

Аутентификация: email-код, опц. пароль, JWT, refresh-токены, rate-limiting, sessions, login attempts.

### 3.2 Промпт

> Реализуй домен **auth** для Aurum Pharma согласно:
> - `docs/spec-v3.md` раздел 2 «Аутентификация»
> - `docs/db-schema-v2.md` раздел 5 «Migration 0002 — auth»
> - `CLAUDE.md` разделы 3, 4, 8
>
> **Этапы:**
>
> ### Шаг 1. Миграция 0002
> - `app_user`, `session`, `email_code`, `login_attempt` точно как в схеме
> - Все индексы как в схеме
> - downgrade удаляет таблицы и FK (включая отложенный из миграции 0003 — его пока нет, не трогай)
>
> ### Шаг 2. Backend домен `app/domains/auth/`
> - `models.py` — SQLAlchemy 2.0 модели для всех 4 таблиц
> - `schemas.py` — Pydantic v2:
>   - `LoginCodeRequest` (email)
>   - `LoginCodeVerify` (email, code, optional password)
>   - `TokenResponse` (access, refresh, expires_in)
>   - `RefreshRequest` (refresh_token)
>   - `MeResponse` (user info)
> - `repository.py` — чисто работа с БД
> - `service.py` — бизнес-логика:
>   - `request_login_code(email, ip)`:
>     - Rate-limit: максимум 1 код в минуту и 10 в час на email
>     - Генерация 6-значного кода
>     - Хеш sha256(code + salt), хранение в email_code
>     - Создаёт запись в login_attempt с outcome='code_requested'
>     - Отправляет email через Celery-задачу `send_email_code` (просто логгер вместо реальной отправки в Этапе 1)
>     - Возвращает success даже если email не существует (anti-enumeration)
>   - `verify_login_code(email, code, password, ip)`:
>     - Проверка rate-limit: 5 неудач за 15 мин → block IP+email на 15 мин
>     - Находит активный email_code, проверяет hash, expires_at
>     - Если у user_assignment.password_required=true — проверяет пароль
>     - Если user не существует — возвращает ошибку (на этом этапе создаются только через support)
>     - Помечает email_code как used
>     - Создаёт session с refresh_token (32 байта random, hash в БД)
>     - Возвращает access_token (JWT) + refresh_token
>     - Записывает login_attempt с outcome='success'
>   - `refresh_token(refresh_token, ip)`:
>     - Находит session по hash(refresh_token), проверяет expires_at и revoked_at
>     - Ротация: revoke старый, выпускает новый refresh
>     - Новый access_token
>   - `logout(refresh_token)`:
>     - Идемпотентный revoke
>     - Очищает Redis-кеш прав (`auth:perms:{user_id}:*`)
>   - `get_current_user_info(user_id)`:
>     - Возвращает app_user + список user_assignment с branch_id и role
> - `router.py` — FastAPI endpoints:
>   - `POST /api/v1/auth/login/code` → `request_login_code`
>   - `POST /api/v1/auth/login/verify` → `verify_login_code`
>   - `POST /api/v1/auth/refresh` → `refresh_token`
>   - `POST /api/v1/auth/logout` → `logout`
>   - `GET /api/v1/auth/me` → `get_current_user_info`
>
> ### Шаг 3. JWT и security
> - `app/core/security.py`:
>   - `create_access_token(user_id, tenant_id, is_developer, is_administrator)` — JWT с TTL 15 мин
>   - `decode_access_token(token)` — verify, returns payload
>   - `hash_password(password)` — bcrypt
>   - `verify_password(password, hash)` — bcrypt
>   - `hash_code(code, salt)` — sha256
>   - `hash_token(token)` — sha256 (без соли — токен сам по себе 256-bit)
>   - `generate_email_code()` — 6-digit numeric
>   - `generate_refresh_token()` — 32 байта random hex
> - **JWT НЕ содержит список permissions** — только user_id, tenant_id, is_developer, is_administrator. Permissions грузятся в `current_user` dep из Redis-кеша
>
> ### Шаг 4. CurrentUser и dependency
> - `app/core/deps.py`:
>   - `class CurrentUser`: user_id, tenant_id, is_developer, is_administrator, permissions (set[str]), branch_assignments (dict)
>   - `async def current_user(token: ..., db: ..., redis: ...) -> CurrentUser` — extract JWT, load assignments from DB, cache permissions in Redis
>   - `get_db()` dependency — выбирает app_engine или support_engine на основе `is_developer or is_administrator`
>   - `get_redis()` dependency — async Redis client
>   - `require_permission(code: str)` — dependency factory, бросает PermissionDeniedError
>
> ### Шаг 5. Celery задачи
> - `app/tasks/auth.py`:
>   - `send_email_code(email, code)` — пока просто structlog.info() с кодом (реальный SMTP в Этапе 2)
>   - `expire_email_codes()` — periodic: удаляет expired email_code старше 24ч
>   - `expire_sessions()` — periodic: удаляет sessions с expires_at < now() - 30d
> - `app/tasks/celery_app.py` beat_schedule:
>   - `expire-email-codes` — каждые 6 часов
>   - `expire-sessions` — раз в сутки в 3:00
>
> ### Шаг 6. Тесты
> - `tests/domains/auth/test_login.py`:
>   - happy path: запрос кода → verify → получение токенов
>   - неверный код → AuthenticationError
>   - 5 неудач → BLOCKED
>   - rate-limit на запрос кода (более 1/мин)
> - `tests/domains/auth/test_refresh.py`:
>   - happy path
>   - используем revoked → AuthenticationError
>   - ротация: старый refresh нерабочий после использования
> - `tests/domains/auth/test_me.py`:
>   - без токена → 401
>   - с токеном → user info
> - Все тесты с реальным PG через `db_session` фикстуру + SAVEPOINT
>
> ### Шаг 7. Чек-лист после реализации
> ```bash
> docker compose exec backend alembic upgrade head
> docker compose exec backend alembic downgrade -1
> docker compose exec backend alembic upgrade head
> docker compose exec backend pytest tests/domains/auth/ -v
> docker compose exec backend ruff check app tests
> docker compose exec backend mypy app
> # Smoke:
> curl -X POST http://localhost:8000/api/v1/auth/login/code \
>   -H "Content-Type: application/json" \
>   -d '{"email":"dev@aurum.tj"}'
> # → проверить логи backend на наличие кода
> ```
>
> Если все зелёные — `git commit -m "feat(auth): email-code authentication, JWT, sessions, rate-limiting"` и push.

### 3.3 Acceptance criteria

- [ ] Миграция 0002 up/down работают
- [ ] 4 эндпоинта auth работают через curl
- [ ] 3+ тестовых файла, минимум 10 тестов, все зелёные
- [ ] JWT не содержит permissions (только id + флаги)
- [ ] Rate-limiting реально блокирует (5 попыток → 429)
- [ ] Ротация refresh-токенов работает (старый невалиден после использования)
- [ ] Celery beat расписание зарегистрировано

### 3.4 Common pitfalls

| Проблема | Решение |
|---|---|
| `app_user.home_tenant_id` FK не создаётся | Не нужен в этой миграции — он добавится в 0003 как отложенный |
| Кэш Redis не очищается при logout | Используй pattern `auth:perms:{user_id}:*` и `SCAN`, не KEYS |
| Bcrypt медленный (>500ms на verify) | Для email-кодов используй sha256, не bcrypt — коды короткие и одноразовые |
| Тесты падают на параллельных запусках | Каждый тест в своём SAVEPOINT через фикстуру |

---

## 4. Домен: foundation (миграция 0003 + код)

### 4.1 Что делать

Тенант, настройки тенанта, точки (branch), кассы (register). Создание тенанта возможно только через support-эндпоинт.

### 4.2 Промпт

> Реализуй домен **foundation** для Aurum Pharma согласно:
> - `docs/spec-v3.md` разделы 3, 8 (касса как сущность), 12 (license)
> - `docs/db-schema-v2.md` раздел 6
>
> **Включи:**
> 1. Миграция 0003: `tenant`, `tenant_settings`, `branch`, `register` + отложенный FK на `app_user.home_tenant_id` (см. раздел 6 схемы)
> 2. Все 4 таблицы с RLS, индексами, триггерами updated_meta
> 3. Домен `app/domains/foundation/`:
>    - Модели для 4 таблиц
>    - Pydantic-схемы: создание/обновление/чтение
>    - Repository, Service, Router
> 4. Эндпоинты:
>    - `POST /api/v1/admin/tenants` — создание тенанта (только support, требует `is_administrator`)
>    - `GET /api/v1/admin/tenants` — список (support)
>    - `GET /api/v1/admin/tenants/{id}` — детали (support)
>    - `PATCH /api/v1/admin/tenants/{id}` — обновление (support)
>    - `GET /api/v1/tenant/settings` — текущие настройки своего тенанта
>    - `PATCH /api/v1/tenant/settings` — обновление (permission: `settings.update`)
>    - `GET /api/v1/branches` — список точек
>    - `POST /api/v1/branches` — создание (permission: `branches.create`)
>    - `GET /api/v1/branches/{id}` — детали
>    - `PATCH /api/v1/branches/{id}` — обновление (permission: `branches.update`)
>    - `DELETE /api/v1/branches/{id}` — soft (permission: `branches.delete`)
>    - аналогично `/api/v1/registers` — 4 эндпоинта для регистров
> 5. Бизнес-правила:
>    - При создании тенанта автоматически создаются `tenant_settings` со значениями по умолчанию
>    - Нельзя деактивировать последнюю активную точку тенанта
>    - Нельзя удалить точку с открытыми сменами (но пока shifts ещё нет — TODO в комментарии)
>    - При создании tenant_settings валидируется `expiry_thresholds`: `yellow >= orange >= red` (yellow самый дальний)
> 6. Celery задача `auto_start_trials` в `app/tasks/foundation.py`:
>    - ежедневно в 4:00
>    - находит wizard_state с `current_step >= 5` и `tenant.status = 'setup'` и `created_at < now() - 60 days`
>    - стартует trial (статус тенанта 'trial', trial_started_at, trial_ends_at = +14 дней)
>    - В beat_schedule в celery_app.py: `auto-start-trials`
> 7. Тесты (минимум 8):
>    - test_create_tenant_creates_default_settings
>    - test_branch_create / list / update / soft-delete
>    - test_register_create / list / update
>    - test_cannot_deactivate_last_active_branch
>    - test_tenant_settings_thresholds_validation (yellow < orange → 422)
>    - test_isolation: пользователь тенанта A не видит branch тенанта B
>    - test_auto_start_trial (mock времени)
>
> Чек-лист, коммит, push — как в auth-домене.

### 4.3 Acceptance criteria

- [ ] Миграция 0003 up/down работают, отложенный FK на app_user добавлен
- [ ] Все 4 таблицы с RLS, видно `\d+ branch` в psql
- [ ] Создание тенанта автоматически создаёт `tenant_settings`
- [ ] Все эндпоинты работают через curl, требуют правильных permissions
- [ ] Celery задача `auto_start_trials` зарегистрирована в beat
- [ ] Тестов 8+, все зелёные
- [ ] Tenant isolation тест проходит (важно!)

---

## 5. Домен: roles (миграция 0004 + код)

### 5.1 Что делать

Support-роли, tenant-роли, шаблоны ролей, конструктор кастомных ролей и назначение ролей пользователям. **Per-user override не входит в Этап 1**.

### 5.2 Промпт

> Реализуй домен **roles** для Aurum Pharma согласно:
> - `docs/spec-v3.md` разделы 1, 4
> - `docs/db-schema-v2.md` раздел 7
> - `CLAUDE.md` раздел 3.4
>
> **Включи:**
> 1. Миграция 0004: `permission`, `role`, `role_permission`, `user_assignment`
> 2. **Seed данных в той же миграции** (через `op.bulk_insert`):
>    - Все 45 permissions из `spec-v3.md` раздел 4.2
>    - начальные системные роли: `developer (level=1)`, `administrator (level=2)`, `owner (level=3)`, `seller (level=4)`
>    - последующие миграции `0019..0023` добавляют `role_template`, переводят `owner`/`seller` в tenant-scoped роли `Владелец`/`Кассир`, добавляют `slug` шаблонов
>    - `role_permission` для ролей по матрице из `spec-v3.md` раздел 4.3
> 3. Домен `app/domains/roles/`:
>    - Модели
>    - Схемы для permissions, roles, role templates, user_assignment
>    - Repository, Service
> 4. Эндпоинты:
>    - `GET /api/v1/permissions` — список (permission: `users.view` или `roles.assign` или `roles.create` или `roles.update`)
>    - `GET /api/v1/roles` — список системных support-ролей и ролей текущего тенанта (та же gate-логика, что у `/permissions`)
>    - `POST /api/v1/roles` — создать кастомную tenant-role (permission: `roles.create`)
>    - `PATCH /api/v1/roles/{id}` — изменить кастомную tenant-role (permission: `roles.update`)
>    - `GET /api/v1/templates` — шаблоны `Владелец`/`Кассир` для конструктора ролей
>    - `GET /api/v1/users` — список сотрудников тенанта с их назначениями (permission: `users.view`)
>    - `POST /api/v1/users/invite` — пригласить нового сотрудника (permission: `users.invite`)
>      - Создаёт app_user со status='invited' (если не существует) + user_assignment
>      - Если invited впервые — отправляет email-приглашение (Celery `send_invite_email` — пока в лог)
>    - `PATCH /api/v1/users/{id}` — обновить профиль (permission: `users.update`)
>    - `POST /api/v1/users/{id}/block` — блокировка (permission: `users.block`)
>    - `DELETE /api/v1/users/{id}` — soft (permission: `users.delete`)
>    - `POST /api/v1/users/{id}/assignments` — назначить роль на точке (permission: `roles.assign`)
>    - `DELETE /api/v1/users/{id}/assignments/{assignment_id}` — отозвать назначение
> 5. **Anti-escalation:**
>    - Owner НЕ может назначить роль с level < 3 (т.е. не может создать admin или developer)
>    - Administrator НЕ может назначить developer
>    - Developer может всё
>    - Эта проверка — в service.py, чёткая ошибка `BusinessRuleError("Cannot assign role of higher level than your own")`
>    - Для конструктора правило строже: нельзя создать/редактировать роль своего уровня или выше; permission-set должен быть подмножеством прав актёра, кроме support-пользователей; каждый permission должен подходить уровню роли (`min_level_required >= role.level`)
> 6. **Эффективные права в Redis:**
>    - При логине / назначении роли — расчёт `effective_permissions = role_permissions` (в Этапе 1 без override)
>    - Сохранение в Redis: ключ `auth:perms:{user_id}:{tenant_id}`, TTL 5 минут
>    - При изменении user_assignment — инвалидация кеша (`DEL`)
>    - При login — загрузка кеша или пересчёт
> 7. Расширь `current_user` dep (`app/core/deps.py`) — теперь populates permissions из Redis или БД
> 8. Тесты (минимум 8):
>    - test_seed_permissions_count (проверка что seed создал 45 permissions)
>    - test_seed_system_roles (4 роли с правильными уровнями и permissions)
>    - test_invite_user_creates_app_user_and_assignment
>    - test_assign_role_to_existing_user
>    - test_anti_escalation_owner_cannot_create_admin
>    - test_anti_escalation_admin_cannot_create_developer
>    - test_developer_can_assign_anything
>    - test_redis_cache_invalidation_on_role_change
>    - test_owner_creates_tenant_role_from_subset
>    - test_template_cannot_bypass_anti_escalation
>    - test_isolation: owner тенанта A не видит users тенанта B
>
> Чек-лист, коммит, push.

### 5.3 Acceptance criteria

- [ ] Миграция 0004 + seed работают
- [ ] В БД 45 permissions, 2 support-роли, tenant-роли и шаблоны ролей
- [ ] Эндпоинты управления пользователями работают
- [ ] Конструктор ролей работает через `POST/PATCH /roles` и `GET /templates`
- [ ] Anti-escalation реально блокирует (тест!)
- [ ] Redis-кеш инвалидируется при изменении назначения
- [ ] `current_user.permissions` правильно загружается из Redis или БД

---

## 6. Домен: catalog (миграция 0005 + код)

### 6.1 Что делать

Tenant catalog (товары), штрихкоды, Excel-импорт с dry-run и откатом 24h.

### 6.2 Промпт

> Реализуй домен **catalog** для Aurum Pharma согласно:
> - `docs/spec-v3.md` раздел 5
> - `docs/db-schema-v2.md` раздел 8
>
> **Включи:**
>
> 1. Миграция 0005: `master_catalog` (пустая в Этапе 1, нужна для будущего), `tenant_catalog`, `barcode`, `catalog_import_job`
> 2. Домен `app/domains/catalog/`:
>    - Модели для 4 таблиц
>    - Pydantic-схемы (включая отдельные для импорта)
>    - Repository с методами поиска через trigram
>    - Service
> 3. Эндпоинты:
>    - `GET /api/v1/catalog` — список с фильтрами (q, category, dispensing_type, page, page_size), формат ответа `{items, total, page, page_size}`
>    - Поиск `q` ищет по brand_name И inn через trigram (`%` оператор)
>    - `GET /api/v1/catalog/{id}` — детали с штрихкодами
>    - `POST /api/v1/catalog` — создать товар (permission: `catalog.create`)
>    - `PATCH /api/v1/catalog/{id}` — обновить (permission: `catalog.update`)
>    - `DELETE /api/v1/catalog/{id}` — soft-delete (permission: `catalog.delete`)
>    - `GET /api/v1/catalog/by-barcode/{code}` — найти товар по штрихкоду (для кассы)
>    - `POST /api/v1/catalog/{id}/barcodes` — добавить штрихкод (permission: `catalog.update`)
>    - `DELETE /api/v1/catalog/{id}/barcodes/{barcode_id}` — удалить штрихкод
> 4. Импорт Excel/CSV:
>    - `POST /api/v1/catalog/import/upload` — загрузка файла (multipart), сохранение в MinIO путь `{tenant_id}/imports/{job_id}.xlsx`, создание `catalog_import_job` со status='pending'
>    - `POST /api/v1/catalog/import/{job_id}/preview` — dry-run: парсинг первых 100 строк, возврат превью + статистики ошибок. Сохранение в `catalog_import_job.preview_data`
>    - `POST /api/v1/catalog/import/{job_id}/confirm` — запуск Celery-задачи `import_catalog_job(job_id, duplicate_strategy)`. Возврат job-info, фронт долго опрашивает status
>    - `GET /api/v1/catalog/import/{job_id}` — статус
>    - `POST /api/v1/catalog/import/{job_id}/rollback` — откат, доступен 24h после finished_at. Soft-deletes всех `tenant_catalog` созданных в этой job (нужно ссылаться на job_id — добавь поле `import_job_id UUID NULL` в tenant_catalog в миграции 0005)
> 5. Celery задача `import_catalog_job`:
>    - читает файл из MinIO (openpyxl для xlsx, csv для CSV)
>    - UTF-8 и Windows-1251 поддержка для CSV
>    - маппинг колонок: автоматический по заголовкам (brand_name, inn, manufacturer, form, dosage, pack_size, atx_code, dispensing_type, storage_type, category, base_price, barcode)
>    - для каждой строки: валидация → создание `tenant_catalog` (и `barcode` если указан)
>    - duplicate_strategy: skip / update / create_copy. Дубликат = по brand_name + manufacturer + dosage + pack_size
>    - в конце: обновление `catalog_import_job.status = 'success'/'failed'`, `expires_at_for_rollback = now() + 24h`
> 6. Тесты (минимум 8):
>    - test_create_catalog_item
>    - test_search_by_brand_trigram (запрос «амикс» → находит «Амиксин»)
>    - test_search_by_barcode
>    - test_unique_barcode_per_tenant
>    - test_import_excel_happy_path (с подготовленным маленьким xlsx)
>    - test_import_preview_returns_errors
>    - test_import_rollback_works
>    - test_import_rollback_blocked_after_24h
>    - test_isolation
>
> Чек-лист, коммит, push.

### 6.3 Acceptance criteria

- [ ] Поиск работает через trigram, время ответа < 200ms на 1K записей
- [ ] Импорт Excel работает с тестовым файлом ≥50 строк
- [ ] Откат импорта реально soft-deletes созданные товары
- [ ] Уникальность штрихкода в рамках тенанта enforced

---

## 7. Домен: inventory (миграция 0006 + код)

### 7.1 Что делать

Партии (batch), движения партий (batch_movement), списания (write_off). FEFO-логика.

### 7.2 Промпт

> Реализуй домен **inventory** для Aurum Pharma согласно:
> - `docs/spec-v3.md` раздел 6
> - `docs/db-schema-v2.md` раздел 9
>
> **Включи:**
>
> 1. Миграция 0006: `batch`, `batch_movement`, `write_off` + триггер `trg_update_batch_qty` на batch_movement
> 2. Домен `app/domains/inventory/`
> 3. Эндпоинты:
>    - `GET /api/v1/batches` — список с фильтрами (catalog_id, branch_id, expiry_status, show_empty=false), формат `{items, total, ...}`
>    - **Важно:** используй view `v_batch_with_expiry_status` для расчёта статуса
>    - `GET /api/v1/batches/{id}` — детали + последние 20 движений
>    - `GET /api/v1/batches/{id}/movements` — все движения партии
>    - `POST /api/v1/batches/{id}/write-off` — списание (permission: `batches.write_off`)
>      - Тело: qty, reason, comment
>      - Создаёт `write_off` + `batch_movement` (type=write_off, qty_delta=−qty)
> 4. FEFO-сервис:
>    - `find_batches_fefo(catalog_id, branch_id, qty_needed) -> list[Batch]`
>    - возвращает партии в порядке `expires_at ASC` где `qty_remaining > 0` и `is_blocked = false`
>    - сумма достаточная для qty_needed
>    - **Используется в pos-домене для продаж**, экспортируется из service.py
> 5. Бизнес-правила:
>    - Нельзя списать больше чем `qty_remaining` (БД-триггер бросит исключение → service ловит → BusinessRuleError)
>    - Заблокированные партии (is_blocked=true) не учитываются в FEFO
>    - Просроченные партии — учитываются согласно `tenant_settings.expired_sale_mode`:
>      - `strict` — НЕ учитываются вообще
>      - `warning` — учитываются, но service возвращает флаг `requires_warning=true`
>      - `off` — учитываются как обычные
> 6. Тесты (минимум 7):
>    - test_write_off_decreases_qty
>    - test_write_off_more_than_available_blocked
>    - test_fefo_returns_expired_first_when_warning_mode
>    - test_fefo_excludes_expired_in_strict_mode
>    - test_fefo_excludes_blocked
>    - test_batch_qty_update_via_trigger
>    - test_isolation
>
> Чек-лист, коммит, push.

### 7.3 Acceptance criteria

- [ ] FEFO правильно сортирует по expires_at ASC
- [ ] Триггер БД не даёт qty_remaining уйти в минус
- [ ] write_off создаёт И write_off запись, И batch_movement
- [ ] Режимы expired_sale_mode работают как описано

---

## 8. Домен: suppliers + incoming (миграция 0007 + код)

### 8.1 Что делать

Поставщики, документы прихода, позиции прихода → создание партий при accept. Возврат поставщику.

### 8.2 Промпт

> Реализуй комбинированный домен **suppliers + incoming** для Aurum Pharma согласно:
> - `docs/spec-v3.md` раздел 7
> - `docs/db-schema-v2.md` раздел 10
>
> **Включи:**
>
> 1. Миграция 0007: `supplier`, `incoming_document`, `incoming_item`, `supplier_return`
> 2. Два домена `app/domains/suppliers/` и `app/domains/incoming/` (отдельные папки, но миграция общая)
> 3. Эндпоинты suppliers:
>    - `GET /api/v1/suppliers` (permission: `suppliers.view`)
>    - `POST /api/v1/suppliers` (permission: `suppliers.create`)
>    - `PATCH /api/v1/suppliers/{id}` (permission: `suppliers.update`)
> 4. Эндпоинты incoming:
>    - `GET /api/v1/incoming` — список документов с фильтрами
>    - `POST /api/v1/incoming` — создать draft-документ (permission: `incoming.create`)
>    - `GET /api/v1/incoming/{id}` — детали с items
>    - `PATCH /api/v1/incoming/{id}` — обновить (только если status=draft)
>    - `POST /api/v1/incoming/{id}/items` — добавить позицию
>    - `PATCH /api/v1/incoming/{id}/items/{item_id}` — обновить позицию
>    - `DELETE /api/v1/incoming/{id}/items/{item_id}` — удалить позицию
>    - `POST /api/v1/incoming/{id}/accept` — финальный accept: для каждой позиции создаёт batch и batch_movement (type=incoming, qty_delta=+qty), обновляет `incoming_item.created_batch_id`, status=accepted, accepted_at=now()
>    - `POST /api/v1/incoming/{id}/reject` — пометить как rejected (без создания партий)
> 5. Эндпоинты supplier_return:
>    - `POST /api/v1/suppliers/returns` — создать возврат (permission: `incoming.return`)
>      - Тело: supplier_id, batch_id, qty, reason, comment
>      - Валидация: батч принадлежит этому поставщику (опционально, если source_document.supplier_id == supplier_id) — добавь warning если не совпадает
>      - Создаёт supplier_return + batch_movement (type=supplier_return, qty_delta=−qty)
>    - `GET /api/v1/suppliers/returns` — список с фильтрами (supplier_id, date_from, date_to)
> 6. Бизнес-правила:
>    - Нельзя редактировать accepted/rejected документ
>    - При accept — все позиции должны иметь expires_at в будущем и qty > 0
>    - total_amount пересчитывается при изменении items автоматически (агрегат через триггер или в service.py — твой выбор)
> 7. Тесты (минимум 7):
>    - test_create_incoming_draft
>    - test_add_items_to_draft
>    - test_accept_creates_batches_with_correct_qty
>    - test_cannot_edit_accepted_document
>    - test_accept_with_past_expiry_blocked
>    - test_supplier_return_decreases_batch_qty
>    - test_isolation
>
> Чек-лист, коммит, push.

### 8.3 Acceptance criteria

- [ ] Accept-документ создаёт корректные batches и batch_movements
- [ ] Иммутабельность accepted-документов
- [ ] supplier_return корректно списывает с партии

---

## 9. Домен: pos (миграция 0008 + код)

### 9.1 Что делать

Смены, продажи, позиции, оплаты, прескрипция. **Иммутабельные completed-продажи.**

### 9.2 Промпт

> Реализуй домен **pos** для Aurum Pharma согласно:
> - `docs/spec-v3.md` раздел 8
> - `docs/db-schema-v2.md` раздел 11
>
> **Это самый критичный домен. Иммутабельность продаж — НЕ нарушай.**
>
> **Включи:**
>
> 1. Миграция 0008: `shift`, `sale`, `sale_item`, `sale_payment`, `prescription_log`
> 2. Домен `app/domains/pos/`
> 3. Эндпоинты shift:
>    - `POST /api/v1/shifts/open` — открыть смену (permission: `pos.shift_open`)
>      - body: register_id, opening_cash
>      - проверка: нет открытой смены на этом register
>      - создаёт shift со status=open
>    - `GET /api/v1/shifts/current` — текущая смена пользователя на конкретной кассе
>    - `POST /api/v1/shifts/{id}/close` — закрыть (permission: `pos.shift_close`)
>      - body: closing_cash_actual
>      - расчёт closing_cash_expected = opening_cash + sum(cash payments) − sum(cash refunds)
>      - расчёт closing_difference = actual − expected
>      - агрегация totals по всем продажам смены в JSONB
>      - status=closed, closed_at=now()
>    - `GET /api/v1/shifts/{id}/z-report` — Z-отчёт
> 4. Эндпоинты sale (продажа):
>    - `POST /api/v1/sales` — создать draft-продажу
>      - привязка к открытой смене текущего пользователя
>      - если is_test (тенант в setup-фазе) — флаг is_test=true
>    - `GET /api/v1/sales/{id}` — детали с items и payments
>    - `POST /api/v1/sales/{id}/items` — добавить позицию (permission: `pos.sell`)
>      - body: catalog_id, qty
>      - **FEFO выбор партии:** вызывает `inventory.service.find_batches_fefo()`
>      - если несколько партий нужны для qty — создаёт несколько sale_item (по одной партии каждый)
>      - проверка прескрипции: если `catalog.dispensing_type='prescription'` → возвращает флаг `requires_prescription_log=true` в response
>      - unit_price = batch.sale_price (или из catalog.base_price если batch не задаёт)
>      - total_price = qty × unit_price
>    - `PATCH /api/v1/sales/{id}/items/{item_id}` — изменить qty (только в draft)
>    - `DELETE /api/v1/sales/{id}/items/{item_id}` — удалить (только в draft)
>    - `POST /api/v1/sales/{id}/payments` — добавить оплату (только в draft)
>      - body: payment_method, amount, metadata
>    - `POST /api/v1/sales/{id}/complete` — выбить чек
>      - **Это критический эндпоинт.** Транзакция:
>        1. Проверка: sum(payments) >= total_amount (можно сдачу — > total)
>        2. Проверка: все required prescription_logs созданы (если есть rx items)
>        3. Для каждого sale_item — создание `batch_movement` (type=sale, qty_delta=−qty)
>        4. **Атомарно:** проверка что `batch.qty_remaining >= sale_item.qty` (через `SELECT FOR UPDATE`)
>        5. Если хоть одна проверка падает — ROLLBACK, BusinessRuleError
>        6. status='completed', completed_at=now()
>        7. receipt_number = последовательный в рамках смены (next(register_id, shift_id))
>    - `POST /api/v1/sales/{id}/prescription` — записать рецепт (если требуется)
>      - body: sale_item_id, prescription_number, doctor_name, etc.
> 5. Эндпоинты возврата:
>    - `POST /api/v1/sales/{parent_id}/refund` — создать sale типа return (permission: `pos.refund`)
>      - body: items_to_return (sale_item_id, qty), reason, comment
>      - создаёт новую sale с sale_type='return', parent_sale_id=parent
>      - на parent проставляет voided_at, voided_by_sale_id (если возврат полный — все позиции)
>      - частичный возврат не помечает parent как voided
>      - создаёт batch_movement (type=sale_return, qty_delta=+qty) — возвращает товар на склад
>      - reason обязателен/опционален согласно `tenant_settings.refund_reason_mode`
>    - **Иммутабельность:** PATCH/DELETE на completed sale → 409 ConflictError с понятным сообщением
> 6. Бизнес-правила (КРИТИЧНЫЕ):
>    - Completed sale — никогда не UPDATE/DELETE
>    - Проверка остатка партии — через SELECT FOR UPDATE в одной транзакции с движением
>    - Возврат может быть только частичный (qty <= original qty) или полный
>    - Один и тот же sale_item можно вернуть только один раз (надо проверять сумму уже возвращённого)
>    - Если просроченная партия — режим из tenant_settings (см. inventory)
> 7. Тесты (минимум 12):
>    - test_open_shift / close_shift
>    - test_cannot_open_two_shifts_on_same_register
>    - test_create_draft_sale_in_open_shift
>    - test_add_item_uses_fefo
>    - test_add_item_splits_across_batches_when_qty_exceeds_one
>    - test_complete_sale_decreases_batch_qty
>    - test_complete_sale_with_insufficient_payment_fails
>    - test_complete_sale_with_concurrent_qty_change_handled (через `SELECT FOR UPDATE`)
>    - test_cannot_modify_completed_sale (409)
>    - test_refund_creates_sale_with_parent_id_and_voids_original
>    - test_partial_refund_does_not_void_parent
>    - test_prescription_log_required_for_rx_items
>    - test_test_sale_flag_for_setup_phase
>    - test_isolation
>
> Чек-лист, коммит, push.

### 9.3 Acceptance criteria

- [ ] Иммутабельность — попытка PATCH completed sale → 409
- [ ] FEFO работает корректно при разбиении на несколько партий
- [ ] Concurrent qty проверяется через SELECT FOR UPDATE (демонстрация в тесте)
- [ ] Z-отчёт собирает корректные totals
- [ ] is_test=true для setup-фазы (батчи не списываются — обнови inventory.service)

### 9.4 Critical pitfall

**Гонка остатков:** двое продавцов одновременно продают последнюю упаковку. Без `SELECT FOR UPDATE batch` оба commit-нутся и qty_remaining уйдёт в минус. Триггер бросит исключение — но второй продавец увидит загадочную ошибку. Правильно: `SELECT ... FROM batch WHERE id=X FOR UPDATE` в начале транзакции complete. Это блокирует второго до конца первой.

---

## 10. Домен: billing (миграция 0009 + код)

### 10.1 Что делать

Тарифы, подписки, инвойсы, платежи. **Только банковский перевод в Этапе 1.**

### 10.2 Промпт

> Реализуй домен **billing** для Aurum Pharma согласно:
> - `docs/spec-v3.md` раздел 17.2
> - `docs/db-schema-v2.md` раздел 12
>
> **Включи:**
>
> 1. Миграция 0009: `subscription_plan`, `tenant_subscription`, `invoice`, `payment`
> 2. **Seed plan:** `aurum_pharma` со ценой $50 эквивалент в TJS (значение в .env: `DEFAULT_PRICE_TJS=550`)
> 3. Эндпоинты:
>    - `GET /api/v1/billing/plans` — список тарифов
>    - `GET /api/v1/billing/subscription` — текущая подписка тенанта (`v_active_subscription`)
>    - `GET /api/v1/billing/invoices` — список инвойсов тенанта
>    - `GET /api/v1/billing/invoices/{id}` — детали с платежами
>    - **Admin-only:**
>    - `POST /api/v1/admin/tenants/{id}/subscription` — изменить подписку (создать новую, отменить старую)
>    - `POST /api/v1/admin/tenants/{id}/invoices` — создать инвойс вручную
>    - `POST /api/v1/admin/tenants/{id}/invoices/{id}/payments` — зафиксировать платёж
> 4. Celery задачи в `app/tasks/billing.py`:
>    - `generate_monthly_invoices` — раз в день в 5:00:
>      - находит active subscriptions с period_end < now() + 7 days и без issued invoice
>      - создаёт invoice со amount = price_per_branch × branches_count
>      - отправляет email (пока в лог)
>    - `process_trial_endings` — раз в день в 6:00:
>      - находит trial-подписки с period_end < now()
>      - переводит в grace_period
>      - отправляет email
>    - `process_grace_endings` — раз в день в 7:00:
>      - находит grace_period с period_end + 7days < now()
>      - переводит в suspended → tenant.status='readonly'
> 5. Бизнес-правила:
>    - При изменении количества точек тенанта (creation/deletion в branch) → пересчёт подписки:
>      - месячный тариф: pro-rata доплата при добавлении точки
>      - годовой тариф: бесплатно до конца оплаченного года
>    - **Хук:** добавь Celery-задачу `recalculate_subscription_on_branch_change(tenant_id)` и вызывай её из `branch.service` при create/delete
> 6. Тесты (минимум 6):
>    - test_get_current_subscription
>    - test_create_invoice_for_active_subscription
>    - test_payment_marks_invoice_as_paid
>    - test_trial_to_grace_transition
>    - test_grace_to_readonly_transition
>    - test_branches_change_triggers_subscription_recalc
>    - test_isolation
>
> Чек-лист, коммит, push.

### 10.3 Acceptance criteria

- [ ] Seed создаёт 1 plan
- [ ] Celery beat расписания (3 новые задачи) зарегистрированы
- [ ] Переходы статусов trial → grace → readonly работают
- [ ] Read-only режим (status='readonly') блокирует POS-эндпоинты — добавь проверку в pos-роутере (middleware или dependency)

---

## 11. Домен: audit (миграция 0010 + код)

### 11.1 Что делать

audit_log таблица + триггеры на всех тенантных таблицах + эндпоинты просмотра.

### 11.2 Промпт

> Реализуй домен **audit** для Aurum Pharma согласно:
> - `docs/spec-v3.md` раздел 10
> - `docs/db-schema-v2.md` раздел 13
>
> **Включи:**
>
> 1. Миграция 0010: создание таблицы `audit_log` + триггеры `trg_audit_*` на всех таблицах из раздела 13 схемы
>    - Триггер уже есть как функция (из миграции 0001), нужно только привязать к таблицам
> 2. Домен `app/domains/audit/`
> 3. Эндпоинты:
>    - `GET /api/v1/audit/my` — свой персональный аудит (permission: `audit.view.own`)
>      - фильтры: date_from, date_to, action, table_name, page, page_size
>    - `GET /api/v1/audit/tenant` — аудит всех в тенанте (permission: `audit.view.tenant`)
>      - те же фильтры + user_id
>    - `GET /api/v1/audit/global` — глобальный аудит (permission: `audit.view.global`, только developer)
>      - + фильтр tenant_id
>    - `GET /api/v1/audit/record/{table}/{id}` — история изменений конкретной записи
> 4. Сервис добавляет в audit_log явные записи (помимо триггеров) для:
>    - `audit_log_view(user_id, table_name, record_id)` — просмотр чувствительной записи (закупочная цена партии, рецептурный лог)
>    - `audit_log_export(user_id, what)` — экспорт данных
>    - `audit_log_impersonate(support_user_id, tenant_id)` — support вошёл от имени тенанта
> 5. **PII protection:**
>    - В endpoint-ах audit фильтруй password_hash, totp_secret, refresh_token_hash из old_values/new_values перед возвратом
>    - Список «секретных» полей выноси в константу `SENSITIVE_FIELDS`
> 6. Тесты (минимум 6):
>    - test_insert_creates_audit_record
>    - test_update_logs_only_changed_fields
>    - test_delete_creates_audit_record_with_old_values
>    - test_audit_view_own_excludes_other_users
>    - test_audit_view_tenant_excludes_other_tenants
>    - test_password_hash_filtered_from_audit_response
>    - test_developer_sees_all_tenants
>
> Чек-лист, коммит, push.

### 11.3 Acceptance criteria

- [ ] Триггеры применены к 15+ таблицам (по списку в схеме)
- [ ] Любое изменение в этих таблицах создаёт запись в audit_log
- [ ] changed_fields содержит ТОЛЬКО изменённые поля
- [ ] PII фильтруется в API-ответе

---

## 12. Домен: onboarding (миграция 0011 + код)

### 12.1 Что делать

Setup wizard (8 шагов), чек-лист первых задач, авто-старт trial.

### 12.2 Промпт

> Реализуй домен **onboarding** для Aurum Pharma согласно:
> - `docs/spec-v3.md` раздел 17.1
> - `docs/db-schema-v2.md` раздел 14
>
> **Включи:**
>
> 1. Миграция 0011: `wizard_state`, `onboarding_checklist`
> 2. При создании тенанта (в admin endpoint foundation) — автоматически создавать wizard_state со step=1 и onboarding_checklist (setup_ends_at = created_at + 60 days)
> 3. Эндпоинты:
>    - `GET /api/v1/onboarding/wizard` — текущее состояние
>    - `POST /api/v1/onboarding/wizard/step/{step}` — submit данных шага (тело структуру меняется по шагу)
>      - Шаг 1: профиль аптеки → обновление tenant
>      - Шаг 2: первая точка → создание branch
>      - Шаг 3: реквизиты для чека → обновление branch.receipt_header
>      - Шаг 4: первый сотрудник (себя) → пометить
>      - Шаг 5: загрузка каталога → проверка ≥100 товаров, обновление checklist.catalog_items_count
>      - Шаг 6: настройки кассы → создание register
>      - Шаг 7: регуляторика → обновление tenant_settings.prescription_warning_text
>      - Шаг 8: готово → wizard_state.is_completed=true
>    - `GET /api/v1/onboarding/checklist` — статус чек-листа
>    - `POST /api/v1/onboarding/start-trial` — ручной триггер старта trial
>      - проверка: catalog_items_count >= 100
>      - tenant.status='trial', trial_started_at=now(), trial_ends_at=+14 days
>      - в `tenant_subscription` создаётся запись со status='trial'
> 4. Celery задача `update_checklist_progress` — НЕ нужна, прогресс обновляется в момент действий (callback hooks):
>    - При создании catalog item → обновить catalog_items_count в checklist
>    - При первом incoming → пометить `first_incoming` в completed_tasks
>    - При первой sale → пометить `first_sale`
>    - При втором user_assignment → пометить `second_user`
>    - При первом shift open → пометить `shift_opened`
>    - При завершении первой sale с печатью → пометить `receipt_printed`
> 5. Реализуй callbacks через простую функцию `onboarding.service.track_event(tenant_id, event_name)` и вызывай её из соответствующих сервисов
> 6. Расширь `auto_start_trials` из foundation: проверка `catalog_items_count >= 100`. Иначе — НЕ авто-стартуем, шлём email «нужно загрузить каталог»
> 7. Тесты (минимум 5):
>    - test_wizard_state_created_with_tenant
>    - test_step_5_validates_catalog_count
>    - test_start_trial_blocked_below_100_items
>    - test_start_trial_creates_subscription
>    - test_track_event_updates_checklist
>    - test_isolation
>
> Чек-лист, коммит, push.

### 12.3 Acceptance criteria

- [ ] Wizard и checklist создаются автоматически при тенанте
- [ ] Все 8 шагов обрабатываются
- [ ] Старт trial блокируется при <100 товарах
- [ ] Callbacks из других сервисов обновляют checklist

---

## 13. Домен: notifications (миграция 0012 + код)

### 13.1 Что делать

Внутренние уведомления, доставка email + Telegram, подписки.

### 13.2 Промпт

> Реализуй домен **notifications** для Aurum Pharma согласно:
> - `docs/spec-v3.md` (упоминается в 17.3, 14.2)
> - `docs/db-schema-v2.md` раздел 15
>
> **Включи:**
>
> 1. Миграция 0012: `notification`, `notification_subscription`, `notification_delivery`
> 2. Сервис `notify(tenant_id, user_id, event_type, title, body, data, severity)`:
>    - Создаёт `notification`
>    - Проверяет subscriptions пользователя
>    - На каждый включённый канал — создаёт `notification_delivery` со status='pending'
>    - Кикает Celery-задачу `process_pending_deliveries`
> 3. Эндпоинты:
>    - `GET /api/v1/notifications` — список своих уведомлений (фильтры: unread_only, severity)
>    - `POST /api/v1/notifications/{id}/read` — пометить прочитанным
>    - `POST /api/v1/notifications/read-all` — прочитать все
>    - `GET /api/v1/notifications/subscriptions` — мои подписки
>    - `PATCH /api/v1/notifications/subscriptions` — обновить подписки
> 4. Celery задачи `app/tasks/notifications.py`:
>    - `process_pending_deliveries` — каждую минуту: берёт pending deliveries, отправляет:
>      - email: через SMTP (в Этапе 1 — лог-only)
>      - telegram: через bot API (в Этапе 1 — лог-only, реальная интеграция в Этапе 2)
>      - retry до 3 раз с экспоненциальной задержкой
>    - `purge_old_notifications` — раз в неделю: удаляет read-уведомления старше 30 дней
> 5. Интеграция с другими доменами через `notify()`:
>    - billing: invoice issued, invoice overdue, payment received
>    - foundation: license expiring (точка с license_expires_at < now() + 30 days)
>    - inventory: batch expiry_status reached 'red'
>    - catalog: import job completed/failed
>    - auth: новая сессия с нового IP (опционально)
>    - onboarding: setup_ends_at < now() + 7 days напоминание
> 6. Celery задача `check_expiring_licenses` — раз в день в 8:00 для каждого active тенанта
> 7. Тесты (минимум 5):
>    - test_notify_creates_notification_and_deliveries
>    - test_subscription_disabled_skips_channel
>    - test_delivery_retry_on_failure
>    - test_purge_old_notifications
>    - test_isolation
>
> Чек-лист, коммит, push.

### 13.3 Acceptance criteria

- [ ] Notifications автоматически создаются для событий
- [ ] Каналы доставки уважают user.subscriptions
- [ ] Retry-логика работает для failed deliveries

---

## 14. Frontend: features по доменам

После всех backend-доменов — фронтенд. Подход: одна фича = один промпт, по аналогии с backend-доменами.

Frontend-домены в том же порядке:

1. **frontend/auth** — login страница, AuthGuard, useAuth hook
2. **frontend/foundation** — admin tenants, settings page, branches + registers CRUD
3. **frontend/roles** — users list, invite modal, assignments, roles screen, role builder, templates
4. **frontend/catalog** — list + search, item card, Excel import flow
5. **frontend/inventory** — batches list с цветами, write-off modal
6. **frontend/suppliers** + **frontend/incoming** — список поставщиков, форма прихода
7. **frontend/pos** — главный экран кассы (с шорткатами F2/F3/F4)
8. **frontend/billing** — subscription page, invoices
9. **frontend/audit** — audit log viewer
10. **frontend/onboarding** — setup wizard (8 шагов)
11. **frontend/notifications** — notifications dropdown, subscriptions page
12. **frontend/reports** — 7 базовых отчётов из spec раздел 9

Каждый промпт включает:
- ссылки на API из соответствующего backend-домена
- спецификацию из spec-v3.md
- набор route'ов TanStack Router
- компоненты + features-структуру
- минимум 1 unit-test для нетривиальной логики

Промпты для frontend-доменов мы напишем после реализации первых 3 backend-доменов — увидим как Claude справляется, и адаптируем формат.

---

## 15. E2E тесты (после всех доменов)

Создать `e2e/`-папку с Playwright. Реализовать 13 сценариев из `spec-v3.md` раздел 16.3.

---

## 16. .env.example

Создаётся в `bootstrap` фазе. Эталон:

```bash
# Environment
ENVIRONMENT=development

# Database
POSTGRES_DB=aurum
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres_dev_only
AURUM_APP_PASSWORD=aurum_app_dev_only
AURUM_SUPPORT_PASSWORD=aurum_support_dev_only

# App connection
DATABASE_URL_APP=postgresql+asyncpg://aurum_app:aurum_app_dev_only@postgres:5432/aurum
DATABASE_URL_SUPPORT=postgresql+asyncpg://aurum_support:aurum_support_dev_only@postgres:5432/aurum

# Test database
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres_dev_only@postgres-test:5432/aurum_test

# Redis
REDIS_URL=redis://redis:6379/0

# JWT
JWT_SECRET=change_me_to_random_32_bytes_in_prod
JWT_ALGORITHM=HS256
ACCESS_TOKEN_MINUTES=15
REFRESH_TOKEN_DAYS=7

# CORS
CORS_ORIGINS=["http://localhost:5173","http://localhost:3000"]

# MinIO
MINIO_ENDPOINT=minio:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin_dev_only
MINIO_BUCKET=aurum-files
MINIO_SECURE=false

# Email (заглушки в Этапе 1 — задачи логируют в stdout)
EMAIL_BACKEND=log
EMAIL_FROM=no-reply@aurum-pharma.tj
SMTP_HOST=
SMTP_PORT=
SMTP_USER=
SMTP_PASSWORD=

# Celery
CELERY_BROKER_URL=redis://redis:6379/1
CELERY_RESULT_BACKEND=redis://redis:6379/2

# Pricing
DEFAULT_PRICE_TJS=550

# Frontend (vite reads VITE_-prefixed)
VITE_API_URL=http://localhost:8000/api/v1

# Windows fix
WATCHFILES_FORCE_POLLING=true
```

---

## 17. Сводка по срокам

| Фаза | Содержание | Срок (активного времени) |
|---|---|---|
| Bootstrap репо | Skeleton + Docker + CI | 2–4 часа |
| Backend домены 1–12 | по 2–4 часа каждый | 30–45 часов |
| Frontend домены 1–12 | по 1–3 часа каждый | 20–30 часов |
| E2E тесты | 13 сценариев | 5–8 часов |
| Smoke и фиксы | Финальная отладка | 5–10 часов |
| **ВСЕГО** | | **~70–100 часов активного внимания** |

При темпе 2 часа в день — **2–2.5 месяца календарно**. При полном погружении — 3–4 недели.

К этому добавляется деплой (хостинг, домен, SSL, первый клиент) — отдельная сессия после готового MVP.

---

## 18. После Этапа 1

Когда все 12 доменов готовы и E2E зелёные:

1. **Деплой:** отдельная серия артефактов про Hetzner / Cloudflare / nginx / production .env / бэкапы
2. **Первый пилот:** ручной онбординг одной аптеки в Душанбе
3. **Сбор обратной связи:** 2 недели в режиме «сидим рядом»
4. **Фиксы первой волны:** баги + UX
5. **Второй пилот:** уже с улучшениями
6. **Этап 2 — планирование:** на основе реальной обратной связи решаем что приоритетнее (офлайн / эквайринг / 1С-импорт / расширенный security)

---

## 19. Что делать если застрял

| Ситуация | Действие |
|---|---|
| VS Code Claude не понимает контекст | Скопируй ему весь промпт ещё раз + укажи на конкретные файлы (CLAUDE.md, spec-v3.md) |
| Тесты падают, не понимаешь почему | Запусти `pytest -vvs --tb=long` + покажи лог Claude'у |
| Миграция не откатывается | Проверь downgrade — наверняка пропущен DROP TRIGGER или DROP FUNCTION |
| RLS не пускает в support session | Проверь `app.support_session` GUC: `SHOW app.support_session;` в открытой сессии |
| frontend ругается на типы | `pnpm typecheck` показывает где, скопируй ошибку Claude'у |
| docker compose up застрял | `docker compose logs <service>` — обычно один из сервисов не стартует из-за порта/volume |
| Что-то архитектурное не сходится | Возвращайся ко мне (в этот чат) с описанием — не накручивай Claude в VS Code на сомнительный фикс |

---

## 20. Версии и обновления

При обновлении этого документа:
1. Меняем номер версии в шапке
2. Если меняется промпт уже реализованного домена — фиксируем в `docs/known-issues.md` дрейф и план реконсиляции
3. Если меняется порядок доменов — обновляем все ссылки

---

**Конец документа. Готов к запуску. Удачи.**
