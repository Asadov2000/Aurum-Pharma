# AI context index

Эта карта помогает быстро найти контекст задачи. Она не описывает поведение
продукта и не заменяет код, тесты, ADR или `AGENTS.md`.

## Быстрый старт задачи

1. Прочитать `AGENTS.md`.
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
| RLS и инварианты БД | модели, миграции, PostgreSQL triggers | `backend/tests/isolation/` |
| Backend core/middleware | `backend/app/core/`, `backend/app/middleware/` | `backend/tests/core/`, `backend/tests/middleware/` |
| Фоновые задачи | `backend/app/tasks/` | тесты соответствующего домена |
| Frontend-фича | `frontend/src/features/<domain>/` | `frontend/tests/features/<domain>/` |
| Общий UI | `frontend/src/components/` | `frontend/tests/components/` |
| Общие frontend-механизмы | `frontend/src/lib/`, `frontend/src/stores/` | `frontend/tests/lib/`, тесты фичи |
| Browser flow | `frontend/e2e/*.spec.ts` | изолированный `scripts/e2e-isolated.ps1` |
| Архитектурное решение | `docs/adr/` | код и тесты имеют приоритет для текущего поведения |
| Продуктовые границы | `docs/spec-v3.md` | сверять с реализацией |
| Схема данных | `docs/db-schema-v2.md` | миграции являются текущей истиной |
| Текущее состояние и риски | `docs/development-state.md`, `docs/known-issues.md` | обновлять после широкого аудита или релизного gate |

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
