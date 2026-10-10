# CircleCI Free: полная матрица проверок

Конфигурация переносит обязательные проверки `.github/workflows/ci.yml` в CircleCI.
До успешной проверки всей матрицы это кандидат, а не замена действующего CI gate.
Защиту main нельзя переключать по результату короткого пилота.

| Задание | Проверки |
| --- | --- |
| tooling-linux | Планировщик, readiness/preflight, CORS, broker, TPM, evidence, защитные условия одноразовой БД |
| tooling-windows | PowerShell 7 и 5.1, Edge wrapper, .NET 10 Core tests, TPM probe и WinUI build |
| backend | Legacy upgrade, роли/RLS, migration roundtrip, отрицательные проверки bootstrap, Ruff/Black/Mypy, весь pytest, Edge identity/dispatcher |
| frontend | Typecheck, бренд, lint, весь Vitest, приложение и публичный сайт |
| dependency-audit | Python audit без пропущенных пакетов, JavaScript auditor tests и audit |
| e2e | Изолированный Compose, Chromium, сайт, seed, restart frontend, весь Playwright и JWT/cookie tests |
| production-containers | Compose/TLS, три образа, permissions/network/import, backup/restore, Edge recovery, Trivy, SBOM, Prometheus, Caddy/runtime |
| ci-gate | Успех всех предыдущих заданий и полнота backend evidence |

Backend выполняется одним полным shard без пропуска тестов. JUnit и manifest
передаются gate через workspace. При ошибке, отмене или пропуске предшественника
gate не получает успешный статус.

Run-блоки перенесены в `.circleci/scripts/` с сохранением команд. Вместо сервисов
GitHub используются одноразовые PostgreSQL/Redis на Linux machine. Вместо Docker
Actions — buildx с прежними флагами. PowerShell-тесты запускаются отдельными
процессами, чтобы exit одного теста не пропускал остальные. Рабочие секреты и
общая dev-БД заданиям не передаются. Новые скрипты требуют CircleCI runner.

## Бесплатный режим и включение

- Только Free без карты, покупки кредитов и платного trial.
- All pushes trigger пока отключён. Ручной запуск из codex/circleci-free-pilot;
  config также допускает main после слияния.
- Нет расписания, платного Docker Layer Caching и публикации образов.
- Linux/Windows пилот прошёл; вместе с настройкой потрачено 26 из 30 000 кредитов.
  Это не оценка полной матрицы.
- После полного запуска проверить Compute/Storage/Network в Plan / Usage,
  сократить хранение результатов, затем настроить запуск для PR и main.
- Только после зелёной полной матрицы менять обязательную проверку на
  ci/circleci: ci-gate и отключать заменённый GitHub workflow.
- При запросе карты или исчерпании Free остановиться, оплату не подключать.

Полная облачная валидация пока не подтверждена. Локальная проверка синтаксиса
и защитных условий не заменяет результаты полной матрицы CircleCI.
