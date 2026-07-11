# E2E-тесты (Playwright)

Сквозные тесты гоняют **настоящий браузер (Chromium)** против **живого
docker-стека** — без моков. Это safety-net перед деплоем: проверяем, что
критические пользовательские сценарии работают целиком, от UI до БД.

## Что покрыто

| Файл | Сценарий | Роль |
|---|---|---|
| `auth.spec.ts` | вход dev/owner, неверный код, выход | оба |
| `tenant-setup.spec.ts` | создание тенанта, смена статуса, billing-drawer (подписка→счёт→платёж) | dev |
| `catalog-flow.spec.ts` | создание позиции, штрихкод, trigram-поиск | owner |
| `catalog-import.spec.ts` | импорт `.xlsx` через wizard и проверка строк в каталоге | owner |
| `incoming-flow.spec.ts` | приход draft → позиция → приёмка → партия в `/batches` | owner |
| `owner-onboarding.spec.ts` | создание аптеки и владельца, вход нового владельца по коду | dev |
| `pos-sale.spec.ts` | desktop barcode scanner → корзина, смена → продажа → **FEFO split 5+2** → оплата → desktop cash-drawer → чек → списание партий | owner |
| `pwa.spec.ts` | manifest, install-иконки, service worker без кеширования API/HTML | public |
| `reports-export.spec.ts` | чек PDF, Z-report XLSX, продажи XLSX, остатки XLSX, desktop file-export metadata | owner |
| `runtime-surface.spec.ts` | browser-режим, Windows desktop bridge/user-agent detection, ready-handshake, offline-warning | owner |
| `shift-close-z-report.spec.ts` | смена → продажа → закрытие → `/reports` → бейдж «недостача» | owner |

Всего **11 spec-файлов / 28 тестов**.

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
миграции, создаёт seed-данные, запускает 28 тестов и в любом случае удаляет его
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

`pnpm e2e` и `pnpm e2e:ui` используют адреса dev-стека по умолчанию. Такой
запуск создаёт тестовые записи в выбранной БД и очищает выбранную Redis DB,
поэтому локально предпочитай `pnpm e2e:isolated`. Прямой режим нужен CI и
осознанной диагностике уже поднятой одноразовой среды.

Переменные прямого режима:

| Переменная | Значение по умолчанию |
|---|---|
| `E2E_BASE_URL` | `http://localhost:5173` |
| `E2E_API_URL` | `http://localhost:8000/api/v1` |
| `E2E_POSTGRES_CONTAINER` | `aurum-postgres` |
| `E2E_POSTGRES_DB` | `aurum` |
| `E2E_REDIS_CONTAINER` | `aurum-redis` |

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
  1. проверяет активное назначение tenant-роли **«Владелец»** для
     `owner@aurum.tj` в Demo Pharmacy;
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
