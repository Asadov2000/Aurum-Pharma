# ADR-0026: Доверенный checkpoint внешней WORM-копии

- Статус: принято
- Дата: 2026-08-29

## Контекст

ADR-0023 защищает зашифрованный Restic repository через versioning и Object Lock
`COMPLIANCE`. Однако выбор `latest` после компрометации production host опасен:
злоумышленник с append-only credential не может удалить старую версию, но может
загрузить новую вредоносную. Checksum manifest, созданный тем же production host,
сам по себе не является независимым решением о доверии.

## Решение

1. Off-site uploader после экспорта создаёт неподписанный candidate. Он содержит
   SHA-256, размер и точный S3 `version ID` каждого зашифрованного объекта Restic,
   а также точные версии manifest и object map. Candidate не считается доверенным.
2. Независимый источник доверия получает точные manifest version ID и SHA-256
   напрямую из control plane внешнего storage и создаёт approval на recovery-host.
   Production candidate служит подсказкой оператору, но не является approval и не
   монтируется в verifier.
3. Сетевой verifier с отдельными read-only credentials и `RESTIC_PASSWORD`
   скачивает каждую одобренную WORM-версию, отклоняет небезопасные пути,
   дубликаты, неверные размеры и SHA-256, выполняет полный `restic check
   --read-data` и создаёт неподписанный verified checkpoint.
4. Отдельный оператор передаёт signer SHA-256 конкретного verified checkpoint
   через authorization-каталог, недоступный verifier. Signer без сети копирует
   checkpoint в приватный tmpfs, требует точное совпадение этого digest, проверяет
   scope и подписывает его Ed25519-ключом. Закрытый ключ не передаётся verifier,
   restore или production host. Пара публикуется атомарно одним каталогом.
5. Подписанный checkpoint хранится вне production host. Он фиксирует bucket,
   prefix, export ID, версии candidate/object map, количество объектов, точные
   Restic snapshot IDs и fingerprint публичного ключа.
6. Restore сначала копирует весь атомарный bundle в приватный tmpfs, проверяет
   Ed25519-подпись и scope, затем читает только эту копию. Он скачивает только
   зафиксированные `version ID`, повторно сверяет каждый объект и восстанавливает
   точный snapshot. `latest` не используется.
7. Read-only restore credential не имеет `PutObject`, `DeleteObject`, изменения
   retention или bypass. Uploader не получает закрытый ключ и пароль Restic.

## Границы гарантии

- Код доказывает формат, разделение credentials, проверку подписи и exact-version
  restore. Физическую независимость recovery-host, secret manager, регион и
  юридическую допустимость внешнего storage подтверждает оператор до пилота.
- Подпись означает, что recovery-host технически проверил repository. Решение о
  доверии к конкретному бизнес-моменту и запуск полного service recovery остаётся
  контролируемой операцией дежурного.
- Verifier не имеет доступа к signing authorization. Оператор сверяет export ID,
  provider approval и digest verified checkpoint по независимому каналу; signer
  не является автоматическим API для сетевого verifier.
- Candidate разрешено передавать через недоверенный канал только как уведомление.
  Approval обязан независимо зафиксировать version ID и SHA-256 через control
  plane провайдера. Закрытый ключ нельзя хранить на сетевом verifier или
  production host.

## Последствия

Компрометация append-only uploader больше не заставляет restore выбирать новую
версию объекта. Для закрытия production P0 всё ещё нужны внешний WORM provider,
независимый recovery-host, staging drill полного сервиса и измеренные RPO/RTO.
