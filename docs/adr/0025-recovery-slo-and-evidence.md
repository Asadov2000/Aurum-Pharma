# ADR-0025: Recovery SLO и доказательства восстановления

- Статус: принято
- Дата: 2026-08-29

## Контекст

ADR-0021 и ADR-0023 определили logical backup, PITR и внешний WORM export.
Расписание и Prometheus-метрики позволяют обнаруживать сбои и устаревшие копии,
но длительность отдельной restore-команды не равна времени восстановления
сервиса, а период запуска WAL snapshot сам по себе не доказывает фактический RPO.

## Решение

1. Production host ежедневно запускает полный backup, каждые пять минут - WAL
   snapshot и off-site export. Локальный изолированный restore drill выполняется
   ежемесячно. Задачи имеют host lock и конечный systemd timeout.
2. Textfile exporter публикует время последней попытки и успеха, результат и
   длительность каждого шага. Эти метрики используются для контроля freshness и
   технической длительности, но не публикуются клиентам как гарантия RPO/RTO.
3. Фактический RPO измеряется на независимом staging recovery-host как разница
   между временем аварии и timestamp последней контрольной записи, доступной
   после восстановления.
4. Service RTO начинается в момент объявления аварии и завершается только после
   загрузки выбранной WORM-версии, восстановления PostgreSQL и MinIO, запуска
   backend и успешных health, authentication, RLS и бизнес smoke-проверок.
5. Staging drill восстанавливает строго указанную version ID по доверенному
   manifest, сохранённому вне production host. Read-only restore credentials не
   совпадают с append-only uploader credentials.
6. До трёх успешных drill на объёме пилота пороги RPO 15 минут и service RTO
   60 минут являются предварительными внутренними целями, а не SLA клиенту.

## Последствия

Текущий локальный drill остаётся быстрым регулярным доказательством целостности.
Для разрешения пилота всё ещё нужны внешний WORM bucket, независимый trusted
manifest/version checkpoint, staging recovery-host, Alertmanager receiver,
free-space/lifecycle monitoring и измеренные evidence-отчёты полного RPO/RTO.
