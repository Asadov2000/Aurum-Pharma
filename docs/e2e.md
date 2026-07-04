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
| `pos-sale.spec.ts` | desktop barcode scanner → корзина, смена → продажа → **FEFO split 5+2** → оплата → чек → списание партий | owner |
| `pwa.spec.ts` | manifest, install-иконки, service worker без кеширования API/HTML | public |
| `reports-export.spec.ts` | чек PDF, Z-report XLSX, продажи XLSX, остатки XLSX, desktop file-export metadata | owner |
| `runtime-surface.spec.ts` | browser-режим, Windows desktop bridge/user-agent detection, ready-handshake, offline-warning | owner |
| `shift-close-z-report.spec.ts` | смена → продажа → закрытие → `/reports` → бейдж «недостача» | owner |

Всего **11 spec-файлов / 28 тестов**.

## Предусловия

1. Поднят весь стек:
   ```bash
   docker compose up -d
   docker compose ps   # running; healthcheck есть не у всех сервисов
   ```
2. Бэкенд в режиме `ENVIRONMENT=development` — тогда `/auth/login/code`
   возвращает `dev_code` в ответе, и тесты логинятся без чтения почты.
3. В БД есть базовый dev-seed: пользователи `dev@aurum.tj`,
   `admin@aurum.tj`, `owner@aurum.tj`, активный demo-тенант «Аптека Шифо»
   или «Demo Pharmacy».
4. Зависимости фронта установлены на **хосте** (не в контейнере — Alpine
   не поддерживается Playwright):
   ```bash
   cd frontend
   pnpm install
   pnpm exec playwright install chromium
   ```

## Запуск

```bash
cd frontend
pnpm e2e            # headless, reporter=list
pnpm e2e:ui         # интерактивный UI-режим Playwright
pnpm e2e:report     # открыть HTML-отчёт после прогона
```

Если host-`pnpm` пытается переустановить зависимости перед тестами, запускай
Playwright напрямую:

```powershell
cd frontend
.\node_modules\.bin\playwright.cmd test --reporter=list
```

На Windows можно запустить E2E вместе с локальной smoke-проверкой:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\demo-smoke.ps1 -RunE2E
```

Один конкретный файл:
```bash
pnpm exec playwright test e2e/pos-sale.spec.ts --reporter=list
```

## Как это устроено

- **`playwright.config.ts`** — `baseURL=http://localhost:5173`, один воркер
  (`workers: 1`, `fullyParallel: false`), потому что POS/inventory-тесты
  проверяют глобальное состояние БД. Скриншот + видео + trace сохраняются
  **только при падении**.
- **`e2e/global-setup.ts`** — один раз перед всеми тестами:
  1. назначает `owner@aurum.tj` системную роль **owner** в Demo Pharmacy
     (без этого все tenant-scoped операции отдают 403 — у owner не было
     ни одной `user_assignment`);
  2. кэширует UUID тенанта в `E2E_TENANT_ID`;
  3. **сбрасывает Redis** (`auth:perms:*`) — иначе owner может войти в
     устаревший пустой permission-кэш с TTL 5 мин и получать 403;
  4. чистит rate-limit на логин-код.
- **`e2e/helpers.ts`** — общие утилиты:
  - `apiLogin()` — логин через API (быстрее формы, обходит rate-limit);
  - `loginInBrowser()` — инъекция токенов прямо в `localStorage`, чтобы
    не гонять форму в каждом тесте;
  - `clearLoginRateLimit()` — чистит оба бакета (1/мин и 10/час), которые
    бэкенд проверяет на `/auth/login/code`;
  - `seedBranch / seedRegister / seedSupplier / seedCatalogItem /
    seedAcceptedBatch` — создают данные **через API** (UI для setup'а
    медленный и хрупкий).
- **Изоляция:** каждый тест создаёт свои данные с уникальными именами
  (`uniqueName()` = префикс + timestamp). `afterAll` ничего не удаляет —
  работаем аддитивно, детерминизм не страдает.

## Известные ограничения

- **Только Chromium.** Firefox/WebKit можно добавить в `projects`, когда
  понадобится кросс-браузерность.
- **Запуск с хоста, не из контейнера.** Контейнер фронта на Alpine (musl),
  а Playwright-браузеры собраны под glibc.
- **Rate-limit на логин** требует прямого доступа к Postgres через
  `docker exec aurum-postgres psql` — тесты предполагают, что контейнер
  называется `aurum-postgres` (имя из docker-compose).

## Найденные и починенные баги

- **`trg_set_updated_meta` падал на `tenant`** — триггер безусловно писал
  `NEW.updated_by`, которого нет в таблице `tenant`. Любой PATCH
  `/admin/tenants/{id}` отдавал 500. Починено миграцией
  `0013_fix_updated_by_trigger.py` (обёртка в `EXCEPTION
  WHEN undefined_column`).
- **Модалка не скроллилась** — длинный billing-drawer обрезался по высоте
  без `overflow`. Починено в `components/ui/Modal.tsx`
  (`max-h-[90vh] flex-col` + `overflow-y-auto` на теле).
