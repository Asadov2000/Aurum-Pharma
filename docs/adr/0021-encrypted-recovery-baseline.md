# ADR-0021: Зашифрованная recovery-база и одноразовый restore drill

- Статус: принято
- Дата: 2026-08-22

## Контекст

Production runbook требовал проверенный backup, но в репозитории не было
исполняемой процедуры. Копирование Docker volume не гарантирует согласованность
PostgreSQL или MinIO, а backup без восстановления является неподтверждённым.

## Решение

1. `aurum_backup` является отдельной DB-ролью: `BYPASSRLS` нужен для полной
   tenant-копии, но роль получает только `pg_read_all_data`, один connection и
   не получает write, create, owner или migration capabilities.
2. MinIO backup account имеет только list/read и не может изменять исходный
   bucket. На основном bucket включено versioning.
3. `docker-compose.recovery.yml` создаёт транзакционно согласованный `pg_dump` и
   следующий за ним снимок текущих объектов, формирует manifest и сохраняет всё
   в Restic с отдельным ключом. Межсистемная атомарность не заявляется.
4. Restic repository находится на отдельном host filesystem вне Git и Docker
   volumes. Retention: 7 daily, 4 weekly, 12 monthly snapshots.
5. Restore drill запускает новые PostgreSQL/MinIO volumes в отдельном internal
   network без host-портов, backend, workers, SMTP и платёжных интеграций.
6. Временные файлы находятся в отдельном дисковом scratch, не в RAM; перед
   запуском проверяется минимальный запас свободного места.
7. Drill восстанавливает владельцев и ACL, затем проверяет SHA-256 dump,
   `pg_amcheck`, Alembic revision, число RLS-таблиц, подключения runtime-ролей,
   количество и SHA-256 восстановленных MinIO-объектов.

## Граница гарантии

Эта реализация является проверяемой логической recovery-базой, но не PITR.
Manifest прямо указывает режим согласованности. Для строгой согласованности БД и
объектов требуется окно запрета записей или отдельный доменный протокол. До
пилота дополнительно нужны:

- PostgreSQL WAL archive и PITR с измеренным RPO;
- независимая off-site копия с Object Lock/WORM;
- размещение backup в разрешённой юрисдикции;
- автоматическое расписание restore drill и оповещение о просрочке;
- измерение RPO/RTO на объёме, близком к production.

## Последствия

Обычный runtime не получает backup credentials. Потеря `RESTIC_PASSWORD` делает
копию невосстановимой, поэтому ключ хранится отдельно от сервера и репозитория.
Restore drill никогда не направляется на production volumes или production DB.

## Развитие решения

ADR-0023 добавляет WAL archive, physical base backup, PITR drill и независимый
off-site WORM export. Из перечисленных выше ограничений открытыми остаются
внешний выбор региона и провайдера, мониторинг свежести, расписание off-site
drill и измерение RPO/RTO на production-подобном объёме.
