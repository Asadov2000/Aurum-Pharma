# Aurum Pharma — Инструкции для AI-ассистента

> Этот файл — твой основной контекст. Прочитай его **полностью** перед первым ответом в сессии.
> Когда сомневаешься в чём-то — возвращайся сюда, не выдумывай.

---

## 0. Правила работы (постоянные — действуют в КАЖДОЙ сессии без повтора в промте)

1. **База — одна общая dev-БД. НИКОГДА не дропать и не вайпать.** Схему менять только `alembic upgrade`; **`alembic downgrade` на dev-БД не запускать**. Сид-аккаунты (`dev@`, `admin@`, `owner@`) и шаблоны ролей руками не пересоздавать.
2. **Recon-before-build.** Перед стройкой читать реальный код. Спецификации в `docs/` могли разойтись с кодом — **источник истины — grep по коду**, не спека. Найденные расхождения перечислять в отчёте.
3. **Скоуп.** Никаких попутных рефакторов вне задачи. Идеи улучшений — отдельным списком в отчёте, **не в коде**.
4. **Стоп-лосс.** Если один и тот же шаг падает **два раза подряд** — остановиться и доложить, не импровизировать дальше.
5. **Definition of done.** Полный прогон `pytest` + `vitest` + e2e (перед e2e — `docker compose restart frontend`); `ruff`/`black`/`mypy` + `tsc`/`eslint` — чисто; **один коммит на задачу**; текстовый отчёт с числами тестов и отклонениями.
6. **Эталоны UI не трогать без явного указания:** `SalesPage`/`CatalogPage` (состояния), `DashboardPage`, `BillingPage`, `ReceiptPrintModal` (`black`/`white` — намеренно для термопечати), `OnboardingPage`.

---

## 1. Что это за проект

**Aurum Pharma** — SaaS-система автоматизации аптек для Республики Таджикистан (аналог 1С:Аптека). Мульти-тенантная, по подписке. Целевой рынок — аптеки и сети РТ.

**Статус:** Этап 1 (MVP, 5–6 месяцев). Разработка ведётся **по доменам**, по одному за раз, с обязательным коммитом после каждого.

**Стадия:** код переписывается с нуля после ревизии scope. Старый код в репо был помечен как технический долг и отброшен.

---

## 2. Стек (зафиксирован — НЕ меняй без обсуждения с пользователем)

### Backend
- Python 3.12
- FastAPI 0.115.*
- SQLAlchemy 2.0 (async, через asyncpg)
- Alembic 1.14.*
- Pydantic v2 + pydantic-settings
- Redis 5.2.* (кеш + очереди)
- Celery 5.4.* (фоновые задачи)
- MinIO 7.2.* (файлы)
- structlog 24.4.* (логи)
- prometheus-client (метрики)
- python-jose (JWT)
- passlib[bcrypt] (хеш паролей)
- httpx (внешние запросы)

### Frontend
- React 18.3
- TypeScript strict (включая `noUncheckedIndexedAccess`)
- Vite 5
- TanStack Router (маршрутизация)
- TanStack Query (серверные данные)
- Tailwind CSS 3
- shadcn-style UI (свои примитивы, без полного shadcn/ui)
- Zustand (клиентское состояние)
- React Hook Form + Zod (формы)
- Axios (HTTP)

### База данных
- PostgreSQL 16
- Row Level Security на всех тенантных таблицах
- Расширения: `pgcrypto`, `pg_trgm`, `unaccent`

### Инфраструктура
- Docker Compose (development only)
- Хост-машина: Windows + Docker Desktop
- Хостинг: пока не выбран, deploy-артефакты пишутся позже

### Тесты
- pytest 8.3 + pytest-asyncio 0.24
- httpx (ASGI transport)
- factory-boy 3.3 (фикстуры данных)
- Playwright (E2E, только критичные flow)
- Реальный PostgreSQL в Docker (не testcontainers)

---

## 3. Архитектурные правила (НИКОГДА не нарушай молча)

### 3.1 Мульти-тенантность через RLS
- Каждая тенантная таблица имеет колонку `tenant_id UUID NOT NULL REFERENCES tenant(id)`
- На каждой такой таблице включён RLS с политикой `tenant_isolation`:
  ```sql
  USING (tenant_id = current_tenant_id() OR is_support_session())
  ```
- Функции `current_tenant_id()` и `is_support_session()` читают значения GUC `app.tenant_id` и `app.support_session`
- Эти GUC устанавливаются в middleware из JWT при каждом запросе

### 3.2 Два пула соединений
- `aurum_app` — обычная роль БД, **RLS включён**
- `aurum_support` — роль с `BYPASSRLS`, для разработчиков/администраторов
- Пользователи `is_developer=true` или `is_administrator=true` → используют support pool
- Обычные пользователи → app pool
- **Это критично:** старая ошибка — пытаться обойти RLS через флаги в Python. RLS работает на уровне БД, обходится только через support pool.

### 3.3 Структура backend-домена
Каждый домен — папка `backend/app/domains/<name>/` со следующими файлами:
- `models.py` — SQLAlchemy ORM-модели
- `schemas.py` — Pydantic-схемы (с `model_config = ConfigDict(from_attributes=True)` где валидируются ORM)
- `repository.py` — работа с БД, никакой бизнес-логики
- `service.py` — бизнес-логика, бросает доменные ошибки (наследников `AurumError`), **не знает про FastAPI/HTTP**
- `router.py` — FastAPI-эндпоинты, транслируют исключения в HTTP
- `__init__.py`

### 3.4 Структура frontend-фичи
Каждый домен — папка `frontend/src/features/<domain>/`:
- `api.ts` — функции запросов к backend (axios)
- `queries.ts` — TanStack Query hooks
- `components/` — компоненты фичи
- `types.ts` — TypeScript-типы
- `routes/` (если у фичи свои routes)

Примитивы UI — в `frontend/src/components/ui/`.

### 3.5 Конвенции БД
- snake_case везде
- PK: `id UUID PRIMARY KEY DEFAULT gen_random_uuid()`
- FK: `<entity>_id`
- Время: `TIMESTAMPTZ` в UTC
- Аудит-поля на тенантных таблицах: `created_at`, `updated_at`, `created_by`, `updated_by`
- Soft delete: `deleted_at TIMESTAMPTZ NULL` только на бизнес-критичных таблицах
- JSONB — для гибких данных, схема валидируется через Pydantic в сервисе
- Денежные суммы: `NUMERIC(14, 2)` + `currency TEXT NOT NULL DEFAULT 'TJS'`
- Партиционирование `audit_log` — не упреждающе, добавляем при достижении 10М записей

### 3.6 Иммутабельные продажи
- `sale` со `status = 'completed'` **никогда** не UPDATE-ится
- Отмена/исправление → отдельная `sale`-строка с `parent_sale_id` (возврат)
- `voided_at`, `voided_by_sale_id` помечают связь
- Сервис должен явно отклонять любые UPDATE на completed-продажах

### 3.7 Audit-log через триггеры
- Все изменения тенантных данных пишутся в `audit_log` через PG-триггеры
- НЕ дублируем в application-level логи
- Логи бизнес-операций через structlog — отдельно, без PII

### 3.8 Слои зависимостей (порядок миграций)
```
0001 extensions          (pgcrypto, pg_trgm, unaccent, функции)
0002 auth                (app_user, session, email_code, login_attempt)
0003 foundation          (tenant, tenant_settings, branch, register)
0004 roles               (permission, role, role_permission, user_assignment)
0005 catalog             (master_catalog, tenant_catalog, barcode)
0006 inventory           (batch, batch_movement, write_off, reservation)
0007 suppliers_incoming  (supplier, incoming_document, incoming_item, supplier_return)
0008 pos                 (shift, sale, sale_item, sale_payment, prescription_log)
0009 billing             (subscription_plan, tenant_subscription, invoice, payment)
0010 audit               (audit_log + триггеры)
0011 onboarding          (wizard_state, checklist)
0012 notifications       (notification, notification_subscription)
```

---

## 4. Код-стиль

### Python
- `ruff` (E, F, W, I, B, C4, UP, PL, RUF) + `black` (line-length=100) + `mypy --strict`
- Все публичные функции типизированы
- Абсолютные импорты: `from app.domains.auth.service import AuthService`
- Доменные ошибки наследуются от `AurumError`, имя заканчивается на `Error`
- Каждая Alembic-миграция имеет рабочий `downgrade()`
- Никаких `# type: ignore` без комментария-обоснования
- Никакого `Any` без комментария-обоснования

### TypeScript
- strict mode + `noUncheckedIndexedAccess`
- ESLint type-checked + Prettier
- `@/` алиас для `src/`
- API-типы пишем руками в Этапе 1 (codegen из OpenAPI — Этап 2)
- Формы — только через RHF + Zod, никаких `useState`-форм
- `console.log` запрещён (только `console.warn` / `console.error`)
- Никаких `any` без комментария-обоснования

### Тесты
- Backend: pytest + pytest-asyncio, реальный PostgreSQL в Docker (контейнер `postgres-test`)
- Каждый тест в SAVEPOINT, rollback в teardown (никакой утечки данных между тестами)
- factory-boy для повторяющихся данных, inline-data для одноразовых
- Минимум для нового домена:
  - 1 интеграционный тест happy-path
  - 1 тест бизнес-правила
  - 1 тест изоляции тенантов
- Frontend: Vitest + Testing Library — только для нетривиальной логики, не для каждого `<Button>`

---

## 5. Workflow

### При получении задачи
1. Прочитай `docs/spec-v3.md` (раздел релевантный задаче) и `docs/db-schema-v2.md` (таблицы релевантные)
2. Если задача неоднозначна — задай **ровно один** уточняющий вопрос
3. Если задача противоречит зафиксированным решениям — скажи об этом, дай альтернативу, спроси разрешения продолжить
4. Перед кодом >100 строк — короткий план (3–5 пунктов)
5. Не предлагай 5 вариантов — предлагай 1 (свой лучший выбор) и спрашивай согласие

### После реализации
1. Прогони чек-лист (см. раздел 6 ниже)
2. Если всё зелёное — `git add . && git commit -m "..." && git push`
3. Короткий отчёт пользователю:
   - Что сделано
   - Что отложено (если что-то отложил — почему)
   - Какие команды нужны пользователю для проверки
4. Спроси «следующий домен?»

### Что НЕ делать
- НЕ пиши код без короткого плана для задач >100 строк
- НЕ задавай по 3 вопроса подряд — один за раз
- НЕ переписывай существующее без причины («могу лучше» — это не причина)
- НЕ предлагай рефакторинг между задачами — только если пользователь явно попросил
- НЕ устанавливай зависимости тихо — спроси у пользователя

---

## 6. Чек-лист после каждого домена

После реализации домена — пройди этот чек-лист. Если что-то падает — **стоп**, разбирайся.

```bash
# 1. Поднять окружение
docker compose up -d
docker compose ps  # все healthy?

# 2. Миграция вверх
docker compose exec backend alembic upgrade head
# Ожидается: без ошибок, новая ревизия в logs

# 3. Миграция вниз (rollback работает?)
docker compose exec backend alembic downgrade -1
# Ожидается: без ошибок

# 4. Миграция вверх снова (идемпотентность)
docker compose exec backend alembic upgrade head
# Ожидается: без ошибок

# 5. Тесты домена
docker compose exec backend pytest tests/domains/<name>/ -v
# Ожидается: все зелёные

# 6. Тесты изоляции тенантов
docker compose exec backend pytest tests/isolation/ -v
# Ожидается: все зелёные

# 7. Lint
docker compose exec backend ruff check app tests
docker compose exec backend black --check app tests
docker compose exec backend mypy app

# 8. Frontend (если есть изменения)
docker compose exec frontend pnpm typecheck
docker compose exec frontend pnpm lint
docker compose exec frontend pnpm test

# 9. Smoke через API (см. promтт каждого домена в handoff.md)
curl http://localhost:8000/api/v1/<endpoint>...
```

Только если все 9 шагов зелёные — коммитим.

---

## 7. Git

- **Trunk-based**: одна ветка `main`, никаких feature-веток для соло-разработчика
- **Conventional Commits**:
  - `feat(catalog): add Excel importer`
  - `fix(auth): rotate refresh token correctly`
  - `chore(deps): bump pydantic to 2.9.2`
  - `docs(handoff): add domain 7 prompt`
  - `test(roles): cover anti-escalation edge case`
- `git push` после каждого зелёного коммита
- НИКОГДА: `git push --force`, `git reset --hard` без явной команды пользователя, удаление миграций которые в `main`

---

## 8. Запрещено абсолютно

- Менять зафиксированный стек без обсуждения (FastAPI → Django и т.п.)
- Менять архитектуру (моноблок → микросервисы и т.п.)
- Хардкодить секреты в код — только через `.env`
- Логировать PII: email пользователей в plaintext, телефоны, имена пациентов, содержимое чеков, цены закупок
- Использовать `localStorage` для sensitive токенов (refresh-токен — TODO: httpOnly cookie в Этапе 2)
- Monkey-patching базовых классов SQLAlchemy (`Base.__init_subclass__` и т.п.)
- Добавлять зависимости вне стека выше без обсуждения
- Использовать `*` в `allow_methods` / `allow_headers` CORS вместе с `allow_credentials=True` (нарушение CORS-спеки)
- Парсить URL через `split('://')` и `split('@')` — только `urllib.parse`
- Хранить permissions в JWT payload как массив строк (слишком большой header) — только короткий идентификатор + Redis-кеш

---

## 9. Зафиксированные «осознанные риски»

Не пытайся их «исправить» молча. Если хочешь предложить улучшение — отдельно скажи пользователю.

| # | Риск | Почему принят |
|---|---|---|
| 1 | Пустой старт справочника (нет master-каталога с готовыми ЛС) | YAGNI до первого реального клиента |
| 2 | Юридические документы откладываются на реактив | Бизнес-решение пользователя, не инженерное |
| 3 | Документация ОФД/Минздрава на Этап 3 | Слишком долго ждать ведомства |
| 4 | Только банковский перевод в Этапе 1 (плохой UX) | Эквайринг карт в РТ сложен, отложен |
| 5 | Минимальные точки расширения в БД (только `currency` колонка) | Не over-engineer'им |
| 6 | Полный QA вынесен из Этапа 1 | Soло-фаундер пишет тесты сам |

---

## 10. Коммуникация с пользователем

### 10.1 Язык и тон
- **Язык:** только русский во всех ответах
- **Тон:** прямой, технический, без воды и лести. На «ты»
- Никаких «отличный вопрос», «прекрасная идея»
- Бейджи статусов: ✅ закрыто, ⚠️ риск, 🟢🟡🔴 уровни
- Эмодзи только функциональные (статусы, предупреждения)

### 10.2 Стиль — для новичка
- Пользователь — **не разработчик**. Каждый шаг объясняй простыми словами.
- Когда называешь инструмент или технический термин — добавляй короткое пояснение в скобках при первом упоминании в ответе (например: «healthcheck (проверка, что контейнер жив)», «ORM (библиотека-прослойка между Python и БД)»).
- Никаких жаргонизмов без расшифровки.
- Не валить деталями — давать понятную последовательность шагов.

### 10.3 Автономность (важно)
- **Не задавай уточняющих вопросов.** Если есть выбор между «спросить» и «сделать разумный выбор» — делай выбор и кратко объясни в отчёте, почему именно так.
- Спрашивай **только** если задача физически невозможна без ответа пользователя (нужны креды/секреты, которых нет; пользователь должен подтвердить действие в стороннем сервисе и т.п.).
- **Не жди подтверждения** перед запуском команд (`docker`, `git`, `pytest`, миграции). Действуй сразу.
- Это включает `git add` → `git commit` → `git push` сразу после успешного завершения задачи.
- Если что-то падает — диагностируй сам через `docker compose logs <service>`, чтение файлов, отдельные запросы. Возвращайся к пользователю только с результатом: либо работает, либо упёрся в реально неразрешимое препятствие (нет root-доступа, нет ключей и т.п.).

### 10.4 Формат ответов
- Структурируй заголовками и списками
- Длинные ответы — markdown с заголовками
- Кодовые блоки для технических деталей
- Без «5 вариантов — выбери сам». Один лучший выбор + краткое объяснение в отчёте

### 10.5 Финальный отчёт (обязательно в конце каждой задачи)

Структура:

1. **Что было сломано** (если фикс) или **что попросили** (если фича)
2. **Что починил / сделал** — коротко по сути
3. **Что проверил** — какие команды запускал, какой получил вывод
4. **Что закоммитил** — сообщение коммита, был ли push

---

## 11. Ссылки на другие документы

- `docs/spec-v3.md` — полная функциональная спецификация
- `docs/db-schema-v2.md` — схема БД с DDL, RLS, индексами
- `docs/handoff.md` — план разработки по доменам + готовые промпты
- `.env.example` — все переменные окружения

---

## 12. Версия и история

- v1 — после ревизии scope в мае 2026, scope сокращён с 11–14 мес до 5–6 мес, ~80 → ~55–60 таблиц
- Предыдущий контекст (старый код, артефакты v1/v2) — отброшен. Пишем с нуля под этот документ.

---

**Если ты дочитал до сюда — ответь пользователю по-русски и ссылайся на этот файл когда нужно.**
