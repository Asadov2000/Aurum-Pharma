# CircleCI Free: полная матрица проверок

Конфигурация выполняет обязательную матрицу проверок в CircleCI.
`.github/workflows/ci.yml` сохраняет те же задания как ручной резерв через
`workflow_dispatch`. Обязательный статус в GitHub — `ci/circleci: ci-gate`.
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
- Нет расписания, платного Docker Layer Caching и публикации образов.
- После полного запуска проверять Compute/Storage/Network в Plan / Usage.
- Стандартное хранение artifacts — 30 дней, workspace и cache — 15 дней
  ([CircleCI](https://circleci.com/docs/guides/optimize/persist-data/)).
  Для SBOM сохранять 30 дней согласно ADR-0022.
- При запросе карты или исчерпании Free остановиться, оплату не подключать.

Последовательность активации после проверки кандидата:

1. Получить успех всех восьми заданий из таблицы на актуальном commit PR.
2. Включить автоматические запуски CircleCI для создания и обновления PR из
   веток этого репозитория и push в default branch (`main`). GitHub App pipeline
   не запускает PR из forks; обязательный gate для них нельзя обходить.
   Убедиться, что GitHub получает статус
   `ci/circleci: ci-gate` для проверяемого commit.
3. Оставить GitHub Actions CI как ручной резерв через `workflow_dispatch`,
   сохранив jobs и inputs. Удалить его автоматические `push`, `pull_request`,
   `merge_group` и `schedule`, чтобы не дублировать проверки. Если включена
   очередь слияния, сначала подтвердить проверку её commit в CircleCI.
4. Проверить полную матрицу CircleCI на финальном commit PR после этих правок.
5. Заменить обязательный GitHub check `CI gate` на `ci/circleci: ci-gate`,
   сохранив остальные защиты ветки и не оставляя промежутка без обязательного
   gate. Изменения попадают в `main` только через PR после успешных проверок.

Локальная проверка синтаксиса и защитных условий не заменяет результаты полной
матрицы CircleCI на актуальном commit PR.
