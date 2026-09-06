# E2E-тесты (Playwright)

Сквозные тесты гоняют **настоящий браузер (Chromium)** против **живого
docker-стека**. Критические сценарии проходят целиком от UI до БД; только
явно обозначенные границы внешних устройств и интеграций могут имитироваться.

## Что покрыто

| Файл | Сценарий | Роль |
|---|---|---|
| `auth.spec.ts` | вход, MFA, refresh, сессии, новый вход, доступность, выход | dev/owner |
| `cashier-pos.spec.ts` | филиальная изоляция кассира, смена, продажа и чек | cashier |
| `catalog-flow.spec.ts` | позиция, штрихкод, поиск и необязательное фото | owner |
| `catalog-import.spec.ts` | импорт `.xlsx` через мастер | owner |
| `configurable-filters.spec.ts` | панель условий, запрос API, приватность значений и мобильная прокрутка | owner |
| `incoming-flow.spec.ts` | приход → приёмка → партия | owner |
| `interface-layout.spec.ts` | desktop/mobile/touch layout ключевых экранов | owner |
| `location-management.spec.ts` | создание и управление точками и кассами | owner |
| `owner-employee-account.spec.ts` | полный жизненный цикл сотрудника аптеки | owner |
| `owner-onboarding.spec.ts` | создание аптеки и вход владельца | dev/owner |
| `optional-mfa.spec.ts` | добровольная MFA, настройки и подтверждение действий паролем | owner |
| `owner-role-assignment.spec.ts` | назначение, конфликт области, отзыв и немедленное применение доступа | owner |
| `owner-role-builder.spec.ts` | публикация версий роли и защита от повышения прав | owner |
| `ownership-transfer.spec.ts` | подтверждаемая передача владения аптекой | owner |
| `payment-reconciliation.spec.ts` | безопасное разрешение неопределенной оплаты | owner |
| `payment-settings.spec.ts` | способы оплаты, выбранные владельцем | owner |
| `platform-account-activation.spec.ts` | одноразовая активация приглашения сотрудника | platform |
| `platform-account-lifecycle.spec.ts` | перевыпуск приглашения и жизненный цикл аккаунта | platform |
| `platform-billing.spec.ts` | цены, финансы и защищённые команды биллинга | platform |
| `pos-sale.spec.ts` | сканеры, FEFO, карта, возврат, чек и списание | owner |
| `pwa.spec.ts` | manifest, иконки и безопасное кеширование | public |
| `reports-export.spec.ts` | PDF/XLSX и передача скачивания desktop-host | owner |
| `runtime-surface.spec.ts` | web/Windows bridge и online-only предупреждение | owner |
| `shift-close-z-report.spec.ts` | закрытие смены и Z-отчёт | owner |
| `startup-performance.spec.ts` | состав стартовой загрузки приложения | owner |
| `support-access.spec.ts` | ограниченная support-сессия и её отзыв | platform |
| `sync-center.spec.ts` | управление sync-учётными данными | platform |
| `tenant-setup.spec.ts` | создание и изменение статуса аптеки | dev |
| `user-session-revocation.spec.ts` | отзыв сессий сотрудника владельцем | owner |

Всего **29 spec-файлов / 69 тестов**.

## Предусловия

1. Docker Desktop запущен, порты `15173` и `18000` свободны.
2. Зависимости фронта установлены на **хосте** (не в контейнере — Alpine
   не поддерживается Playwright):
   ```bash
   cd frontend
   pnpm install
   pnpm exec playwright install chromium
   ```

## Безопасный локальный запуск

```powershell
cd frontend
pnpm e2e:isolated
```

Команда поднимает отдельный Docker Compose проект `aurum-e2e-local`, применяет
миграции, создаёт seed-данные, запускает 69 тестов и в любом случае удаляет его
контейнеры и тома. Используются отдельные порты: frontend `15173`, backend
`18000`; общая dev-БД и dev-Redis не затрагиваются.

HTML-отчёт после прогона:

```powershell
cd frontend
pnpm e2e:report
```

На Windows можно запустить E2E вместе с локальной smoke-проверкой:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\demo-smoke.ps1 -RunE2E
```

## Прямой запуск по выбранной среде

`pnpm e2e` и `pnpm e2e:ui` используют порты изолированного стека по умолчанию.
Прямой режим требует явных неизменяемых ID контейнеров PostgreSQL и Redis,
полученных через `docker compose ps --quiet`. До подготовки данных проверяются
Compose-метки: один проект `aurum-e2e-*` и сервисы `postgres` / `redis`.
Имена контейнеров, dev/demo-проекты и смешанные проекты отклоняются до SQL или
очистки Redis. Штатный launcher и CI передают ID автоматически.
Прямой режим нужен для диагностики уже поднятой одноразовой среды;
локально основной путь — `pnpm e2e:isolated`.

Переменные прямого режима:

| Переменная | Значение по умолчанию |
|---|---|
| `E2E_BASE_URL` | `http://localhost:15173` |
| `E2E_API_URL` | `http://localhost:18000/api/v1` |
| `E2E_POSTGRES_CONTAINER` | обязательный ID изолированного контейнера |
| `E2E_POSTGRES_DB` | `aurum` |
| `E2E_REDIS_CONTAINER` | обязательный ID изолированного контейнера |

Один конкретный файл в прямом режиме:

```bash
pnpm exec playwright test e2e/pos-sale.spec.ts --reporter=list
```

## Как это устроено

- **`playwright.config.ts`** — `baseURL` берётся из `E2E_BASE_URL`, один воркер
  (`workers: 1`, `fullyParallel: false`), потому что POS/inventory-тесты
  проверяют глобальное состояние БД. Скриншот + видео + trace сохраняются
  **только при падении**.
- **`e2e/global-setup.ts`** — один раз перед всеми тестами:
  1. проверяет активные аккаунты владельца и кассира с паролями в одной
     демонстрационной аптеке;
  2. кэширует UUID тенанта в `E2E_TENANT_ID`;
  3. **сбрасывает Redis** (`auth:perms:*`) — иначе owner может войти в
     устаревший пустой permission-кэш с TTL 5 мин и получать 403;
  4. чистит rate-limit на логин-код.
- **`e2e/helpers.ts`** — общие утилиты:
  - `apiLogin()` — логин через API (быстрее формы, обходит rate-limit);
  - `loginInBrowser()` — устанавливает защищённую `httpOnly` refresh-cookie;
    access-токен остаётся только в памяти приложения;
  - `clearLoginRateLimit()` — чистит оба бакета (1/мин и 10/час), которые
    бэкенд проверяет на `/auth/login/code`;
  - `seedBranch / seedRegister / seedSupplier / seedCatalogItem /
    seedAcceptedBatch` — создают данные **через API** (UI для setup'а
    медленный и хрупкий).
- **Изоляция:** локальный сценарий создаёт одноразовые БД, Redis и MinIO. Внутри
  одного прогона каждый тест создаёт данные с уникальными именами
  (`uniqueName()` = префикс + timestamp), а после прогона весь одноразовый стек
  удаляется.

## Известные ограничения

- **Только Chromium.** Firefox/WebKit можно добавить в `projects`, когда
  понадобится кросс-браузерность.
- **Запуск с хоста, не из контейнера.** Контейнер фронта на Alpine (musl),
  а Playwright-браузеры собраны под glibc.
- **Rate-limit на логин** требует Docker CLI на хосте: global setup выполняет
  `docker exec` в контейнерах Postgres и Redis. Имена контейнеров задаются через
  `E2E_POSTGRES_CONTAINER` и `E2E_REDIS_CONTAINER`.

## Найденные и починенные баги

- **`trg_set_updated_meta` падал на `tenant`** — триггер безусловно писал
  `NEW.updated_by`, которого нет в таблице `tenant`. Любой PATCH
  `/admin/tenants/{id}` отдавал 500. Починено миграцией
  `0013_fix_updated_by_trigger.py` (обёртка в `EXCEPTION
  WHEN undefined_column`).
- **Модалка не скроллилась** — длинный billing-drawer обрезался по высоте
  без `overflow`. Починено в `components/ui/Modal.tsx`
  (`max-h-[90vh] flex-col` + `overflow-y-auto` на теле).
