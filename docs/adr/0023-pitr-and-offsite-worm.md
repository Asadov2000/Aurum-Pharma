# ADR-0023: PostgreSQL PITR и независимая off-site WORM-копия

- Статус: принято
- Дата: 2026-08-25

## Контекст

ADR-0021 ввёл проверяемый логический backup, но `pg_dump` не является физической
базой для replay WAL. Потеря single-host сервера также уничтожила бы локальный
WAL-архив и могла сделать локальный Restic недоступным.

## Решение

1. PostgreSQL 16 работает с `wal_level=replica`, `archive_mode=on` и
   `archive_timeout=60s`. `archive_command` атомарно пишет в отдельный host
   filesystem, gzip-сжимает, сверяет существующий сегмент и никогда его не
   перезаписывает.
2. Роль `aurum_pitr` имеет только `LOGIN REPLICATION`, два соединения для
   `pg_basebackup -X stream` и не имеет RLS bypass, table/database privileges,
   ownership или членства в других ролях.
3. Ежедневный physical backup создаётся `pg_basebackup --format=plain
   --wal-method=stream`, проверяется `pg_verifybackup` и шифруется существующим
   Restic. Логический backup ADR-0021 сохраняется как независимый второй путь.
4. Завершённые WAL-сегменты каждые пять минут добавляются в зашифрованный Restic.
   Один host lock сериализует logical backup, base backup, WAL snapshot и export.
5. Off-site uploader видит локальный Restic только read-only, не получает
   `RESTIC_PASSWORD` и копирует только уже зашифрованные repository-объекты.
   Каталог `locks/` не экспортируется; неизменяемый checksum manifest загружается
   последним.
6. Внешний bucket обязан иметь Versioning и default Object Lock `COMPLIANCE`.
   Uploader может List/Get/Put, но не Delete, менять retention или обходить lock.
   PostgreSQL, backend и workers не получают off-site credentials.
7. PITR drill разворачивает base backup и WAL только в пустой scratch, использует
   `recovery.signal`, проверяет достижение target, `pg_amcheck` и завершение
   recovery. CI дополнительно доказывает, что запись после target отсутствует.

## Границы гарантии

- RPO определяется `archive_timeout + период WAL snapshot + время export`; до
  измерения на production-подобном объёме заявленный RPO не публикуется.
- Base backup PostgreSQL и MinIO snapshot не являются межсистемно атомарными.
- WAL нельзя автоматически удалять только по возрасту. Очистка разрешена после
  подтверждённого off-site manifest и наличия подходящего проверенного base.
- Провайдер, аккаунт, регион, срок COMPLIANCE retention и юридическая допустимость
  выбираются до пилота и не могут быть доказаны кодом репозитория.
- Компрометация production host не позволяет удалить WORM-версии, но может
  загрузить новые вредоносные версии. Восстановление выбирает последнюю известную
  корректную версию по независимому manifest и журналу drill.

## Последствия

PITR и off-site export больше не являются архитектурными пробелами, но выпуск
пилота по-прежнему требует внешнего bucket, мониторинга freshness/free-space и
измеренных RPO/RTO. `RESTIC_PASSWORD` хранится отдельно от сервера и WORM bucket.
