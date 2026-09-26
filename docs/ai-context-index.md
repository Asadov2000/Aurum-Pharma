# AI context index

Эта карта помогает быстро найти контекст задачи. Она не описывает поведение
продукта и не заменяет код, тесты, ADR или `AGENTS.md`.

## Быстрый старт задачи

1. Прочитать `AGENTS.md`.
   При первом входе в репозиторий открыть [карту проекта](project-structure.md);
   очередность новых блоков находится в [плане развития](implementation-roadmap.md).
2. Выполнить `scripts/verify-change.ps1 -Mode Auto -PlanOnly`.
3. Искать нужные символы и файлы через `rg`, не читать домен целиком.
4. До редактирования записать краткий task brief:
   - цель и наблюдаемое поведение;
   - что нельзя менять;
   - критичные инварианты;
   - критерии приемки;
   - проверки.
5. Во время разработки использовать `scripts/verify-change.ps1 -Mode Quick`.
6. Перед commit использовать `scripts/verify-change.ps1 -Mode Auto`.
7. `Quick` не заменяет `Auto`, а локальные проверки не заменяют GitHub `CI gate`.

Результат последнего локального запуска: `.codex/verification/latest.json`
и `latest.md` ([формат и запуск в один клик](testing.md)). Сначала читайте
краткий отчёт, затем только лог упавшего шага. Сверяйте время запуска, revision
и текущий `git status`: старый отчёт не подтверждает новые изменения.

Показать план без запуска тестов:

```powershell
.\scripts\verify-change.ps1 -Mode Auto -PlanOnly
```

Полная локальная матрица перед релизом:

```powershell
.\scripts\verify-change.ps1 -Mode Full
```

## Карта кода

| Задача | Реализация | Тесты |
|---|---|---|
| Backend-домен | `backend/app/domains/<domain>/` | `backend/tests/domains/<domain>/` |
| Почта/пароль, SMS-вход и контакты | `backend/app/domains/auth/`, `frontend/src/features/auth/`; [ADR-0042](adr/0042-account-login-identities.md), [Payom](runbooks/payom-sms.md) | auth pytest, Vitest, `frontend/e2e/login-identities.spec.ts`; реальные SMS проверяются отдельно |
| RLS и инварианты БД | модели, миграции, PostgreSQL triggers | `backend/tests/isolation/` |
| Контракт и алгоритм автономного входа v1 | [offline-auth-lease-v1.md](offline-auth-lease-v1.md), `backend/app/domains/sync/offline_auth_lease.py`, доверенное состояние в `offline_auth_lease_state.py`, модели lease в `schemas.py`; runtime запрещает вход | `backend/tests/domains/sync/test_offline_auth_lease_v1.py`, `test_offline_auth_lease_pipeline_v1.py`, V0-регрессии |
| Backend core/middleware | `backend/app/core/`, `backend/app/middleware/` | `backend/tests/core/`, `backend/tests/middleware/` |
| Фоновые задачи | `backend/app/tasks/` | тесты соответствующего домена |
| Frontend-фича | `frontend/src/features/<domain>/` | `frontend/tests/features/<domain>/` |
| Отчёты, выгрузки и финансовая сводка | [Проверка отчётности](reporting-audit.md), `backend/app/domains/pos/`, `backend/app/domains/dashboard/`, `frontend/src/features/reports/` | pytest POS/dashboard/billing, Vitest reports/dashboard, E2E выгрузок и закрытия смены |
| Публичный сайт и способы установки | `website/src/`, `website/scripts/`; [ADR-0041](adr/0041-public-website-boundary.md) | `website/scripts/test.mjs`, браузерная проверка сайта, CI gate |
| Границы компонентов и план развития | [project-structure.md](project-structure.md), [implementation-roadmap.md](implementation-roadmap.md) | отличать реализованные пути и проверенные результаты от планов |
| Windows-клиент / будущий установщик | `desktop/AurumPharma.Desktop/`, `desktop/AurumPharma.Desktop.Core/`; [desktop-host-implementation.md](desktop-host-implementation.md), [desktop-bridge.md](desktop-bridge.md) | desktop core tests, packaged WinUI build, `scripts/windows-host-readiness.ps1`; установщик и offline runtime ещё не выпущены |
| Автономность точки до 30 дней | [ADR-0043](adr/0043-thirty-day-branch-edge-continuity.md), [контракт автономной работы](offline-operations-contract.md), `backend/app/domains/sync/` | fault matrix, Edge integration, Windows и recovery-тесты; текущий runtime остаётся fail-closed |
| Общий UI | `frontend/src/components/` | `frontend/tests/components/` |
| Общие frontend-механизмы | `frontend/src/lib/`, `frontend/src/stores/` | `frontend/tests/lib/`, тесты фичи |
| Browser flow | `frontend/e2e/*.spec.ts` | изолированный `scripts/e2e-isolated.ps1` |
| Тестовая инфраструктура и быстрый цикл | `docs/testing.md`, `docs/e2e.md` | fixtures backend, Vitest environments, E2E guards |
| Архитектурное решение | `docs/adr/` | код и тесты имеют приоритет для текущего поведения |
| Продуктовые границы | `docs/spec-v3.md` | сверять с реализацией |
| Схема данных | `docs/db-schema-v2.md` | миграции являются текущей истиной |
| Текущее состояние и риски | `docs/development-state.md`, `docs/known-issues.md` | обновлять после широкого аудита или релизного gate |
| Юридический и пилотный допуск | `docs/compliance/pilot-readiness-manifest.json`, `docs/compliance/pilot-legal-gate.md` | `scripts/Test-PilotReadiness.ps1 -Mode Validate`; `Enforce` обязан падать до подписанного допуска |

Точные версии зависимостей находятся только в `backend/pyproject.toml`,
`backend/poetry.lock`, `frontend/package.json` и `frontend/pnpm-lock.yaml`.

## Короткая передача задачи

Передача между AI-задачами должна занимать не больше 10-12 строк:

```text
Цель:
Ветка / worktree:
Готово:
Измененные файлы:
Принятые решения:
Проверки и результат:
Текущий блокер (если есть):
Следующее конкретное действие:
```

Не вставлять успешные логи, большие diff и содержание уже принятых документов.
Для ошибки сохранять название упавшего шага и читать только его отдельный лог.
