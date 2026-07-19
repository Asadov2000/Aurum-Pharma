# Постоянный showcase/demo-стек

`docker-compose.demo.yml` поднимает отдельную локальную среду `aurum-demo`:
PostgreSQL с БД `aurum_demo`, Redis, MinIO, backend, Celery и frontend.
Контейнеры, сеть и volumes не используются обычной dev-средой.

Все опубликованные порты привязаны только к `127.0.0.1`. Секреты в compose-файле
не хранятся: при первом настоящем запуске скрипт создаёт
`.env.showcase.local` с криптографически случайными значениями. Файл исключён
из Git и доступен в Windows только текущему пользователю и `SYSTEM`.

## Запуск

Из корня проекта:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-showcase-demo.ps1
```

Скрипт:

1. Проверяет Docker и отсутствие внешних переменных, способных подменить секреты.
2. Создаёт или проверяет `.env.showcase.local`, не выводя значения в терминал.
3. Проверяет fail-closed конфигурацию Docker Compose.
4. Останавливает только известные обычные контейнеры, занимающие те же порты.
5. Не удаляет их, общую dev-БД или какие-либо volumes.
6. Собирает и запускает изолированный demo-стек.
7. Выполняет `alembic upgrade head` только в БД `aurum_demo`.
8. Запускает `python -m app.seed_showcase --profile realistic`.

Backend получает дополнительный явный guard `AURUM_SHOWCASE_SEED=1`. Seeder
также самостоятельно требует `ENVIRONMENT=development`, подключение через
`aurum_support` и точное имя БД `aurum_demo`. На другом окружении или имени БД
он завершится до записи данных.

Инфраструктуру можно проверить без наполнения:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-showcase-demo.ps1 -SkipSeed
```

Для повторного быстрого запуска без пересборки образов добавьте `-SkipBuild`.
`-DryRun` только показывает будущие команды и не создаёт файл с секретами.

После запуска:

- frontend: `http://localhost:5173`;
- API: `http://localhost:8000/docs`;
- MinIO: `http://localhost:9001`.

Тестовые входы:

- владелец: `owner@aurum.tj` / `Owner1234`;
- фармацевт-кассир: `cashier-1@showcase.aurum.invalid` / `DemoUser1234`.

Профиль `realistic` содержит три точки, пять активных касс, 14 сотрудников,
537 позиций каталога, 340 приходов, более 6 000 партий и около 30 000 продаж за
последний год. В нём есть возвраты, рецептурный отпуск, списания, возвраты
поставщикам, уведомления, биллинг и разные статусы аптек. Повторный запуск
распознаёт уже готовый набор и не дублирует данные.

Независимая проверка целостности:

```powershell
docker compose --env-file .\.env.showcase.local --project-name aurum-demo --file .\docker-compose.demo.yml exec -T backend python -m app.validate_showcase
```

Чтобы остановить showcase без удаления данных:

```powershell
docker compose --env-file .\.env.showcase.local --project-name aurum-demo --file .\docker-compose.demo.yml stop
```

Чтобы временно вернуться к прежней dev-среде, сначала остановите showcase
командой выше, затем выполните `docker compose up -d`. Данные обеих сред
сохраняются в разных volumes.

## Правила для локальных секретов

- Не коммитьте и не отправляйте `.env.showcase.local` другим людям.
- `.env.showcase.example` содержит только перечень переменных и не используется
  для запуска.
- Не удаляйте `.env.showcase.local`, пока существуют demo-контейнеры или
  volumes: пароли связаны с уже созданными PostgreSQL, Redis и MinIO.
- Если файл потерян, скрипт завершится до изменений Docker и не создаст
  несовместимые новые ключи.
- Не используйте `down --volumes`: профиль `realistic` должен сохраняться между
  запусками.

## Ограничение демонстрационных данных

Названия организаций, сотрудников, контакты и операции синтетические. Каталог
нужен для разработки интерфейсов и бизнес-сценариев; он не является официальным
реестром зарегистрированных в Таджикистане лекарств и не должен переноситься в
production. Перед пилотом каталог импортируется из подтверждённого официального
или лицензированного источника.
