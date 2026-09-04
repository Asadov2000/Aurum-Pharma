# Production deployment runbook

Статус: подготовленный single-host контур. Он не разрешён для пилота, пока не
закрыты оставшиеся P0-блокеры в `docs/security-release-plan.md`.

## 1. Требования

- Linux-сервер с Docker Engine, Docker Compose v2 и `flock` (`util-linux`).
- DNS `A/AAAA` production-домена указывает на сервер.
- Firewall публикует только `80/tcp`, `443/tcp`, `443/udp` и ограниченный SSH.
- Каталоги конфигурации и секретов находятся вне Git checkout.
- Перед любым обновлением БД существует проверенная резервная копия.
- Для штатного генератора секретов нужен PowerShell 7 на доверенной
  операторской машине или production-сервере.

Не ставьте Cloudflare или другой proxy перед Caddy, пока его CIDR и режим
передачи client IP не добавлены отдельным изменением. Cloudflare Flexible
запрещён; допустим только Full Strict.

## 2. Несекретная конфигурация

Скопируйте `.env.production.example` за пределы репозитория, например в
`/etc/aurum/production.env`, и заполните домен, SMTP и размеры процессов.

Файл не содержит паролей, но всё равно должен принадлежать оператору:

```bash
sudo install -d -m 700 /etc/aurum
sudo install -m 600 .env.production.example /etc/aurum/production.env
```

`AURUM_PUBLIC_ORIGIN` должен быть точным HTTPS origin без завершающего `/`.
`AURUM_DOMAIN` содержит только hostname.

## 3. Секреты

Скрипт создаёт независимые криптографические значения, не печатает их и
отказывается писать внутрь репозитория или поверх существующих файлов. Пароль
SMTP вводится как SecureString:

```powershell
$smtp = Read-Host "SMTP password" -AsSecureString
pwsh ./scripts/New-ProductionSecrets.ps1 `
  -OutputDirectory /etc/aurum/secrets `
  -EmailPassword $smtp
```

Не копируйте секреты в `.env`, Compose arguments, shell history, issue или чат.
Сделайте зашифрованную резервную копию MFA-ключа отдельно от основной БД.
Ключ `RESTIC_PASSWORD` храните отдельно и от сервера, и от backup repository.

Off-site access key выдаёт внешний S3-compatible провайдер. Не передавайте его
PostgreSQL или приложению. Запишите пару в отдельный каталог:

```powershell
$access = Read-Host "Off-site access key" -AsSecureString
$secret = Read-Host "Off-site secret key" -AsSecureString
sudo pwsh ./scripts/New-OffsiteBackupSecrets.ps1 `
  -OutputDirectory /etc/aurum/offsite-secrets `
  -AccessKey $access `
  -SecretKey $secret
```

Read-only restore credentials создаются у провайдера отдельно и не выдаются
production host. На независимом recovery-host запишите их с другим назначением:

```powershell
$access = Read-Host "Off-site restore access key" -AsSecureString
$secret = Read-Host "Off-site restore secret key" -AsSecureString
sudo pwsh ./scripts/New-OffsiteBackupSecrets.ps1 `
  -OutputDirectory /etc/aurum/offsite-restore-secrets `
  -AccessKey $access `
  -SecretKey $secret `
  -Role Restore
```

Ed25519 private key создаётся только на независимом trust/recovery-host. Его
публичную часть передайте на restore-host, закрытую часть не копируйте на
production:

```bash
sudo install -d -m 0700 /etc/aurum/recovery-trust-secrets
sudo openssl genpkey -algorithm Ed25519 \
  -out /etc/aurum/recovery-trust-secrets/AURUM_RECOVERY_SIGNING_PRIVATE_KEY.pem
sudo openssl pkey \
  -in /etc/aurum/recovery-trust-secrets/AURUM_RECOVERY_SIGNING_PRIVATE_KEY.pem \
  -pubout \
  -out /etc/aurum/recovery-trust-secrets/AURUM_RECOVERY_SIGNING_PUBLIC_KEY.pem
sudo chown -R 10001:10001 /etc/aurum/recovery-trust-secrets
sudo chmod 0600 /etc/aurum/recovery-trust-secrets/*.pem
```

## 4. Проверка конфигурации

Сначала выполните fail-closed preflight на production Linux-хосте. Он проверяет
точное соответствие домена и HTTPS origin, отсутствие шаблонных адресов, внешние
и раздельные каталоги, полный комплект секретов, отсутствие ссылок и доступ к
секретам только владельцу. Значения секретов не выводятся:

```bash
pwsh ./scripts/Test-ProductionHostPreflight.ps1 \
  -EnvFile /etc/aurum/production.env
```

Параметры `-SkipUnixPermissionCheck` и `-SkipBackupMountCheck` предназначены
только для изолированных тестов валидатора и запрещены при реальном
развёртывании. `AURUM_BACKUP_FILESYSTEM_ROOT` должен быть отдельной смонтированной
файловой системой; одного существующего каталога недостаточно. После успешного
preflight из корня проекта выполните проверку Compose:

```bash
docker compose \
  --env-file /etc/aurum/production.env \
  --file docker-compose.production.yml \
  config --quiet
```

Проверьте, что итоговая модель публикует порты только у `gateway`. Команду
`docker compose config` без `--quiet` не сохраняйте в общий лог.

## 5. Первый запуск

Сначала соберите образы и поднимите хранилища:

```bash
docker compose \
  --env-file /etc/aurum/production.env \
  --file docker-compose.production.yml \
  build --pull backend gateway

docker compose \
  --env-file /etc/aurum/production.env \
  --file docker-compose.production.yml \
  up -d postgres redis minio
```

Примените только upgrade-миграцию. `alembic downgrade` на production запрещён:

```bash
docker compose \
  --env-file /etc/aurum/production.env \
  --file docker-compose.production.yml \
  --profile maintenance run --rm db-role-bootstrap

docker compose \
  --env-file /etc/aurum/production.env \
  --file docker-compose.production.yml \
  --profile maintenance run --rm migrate
```

После успешной миграции:

```bash
docker compose \
  --env-file /etc/aurum/production.env \
  --file docker-compose.production.yml \
  up -d
```

Caddy автоматически получает и обновляет сертификат. Его volumes `caddy-data`
и `caddy-config` нельзя удалять при обычном обновлении.

## 6. Приёмочная проверка

```bash
docker compose \
  --env-file /etc/aurum/production.env \
  --file docker-compose.production.yml \
  ps

curl --fail --proto '=https' --tlsv1.2 \
  "https://pharmacy.example.com/healthz"

ss -lntup
```

В `ss` не должно быть host-портов `5432`, `6379`, `8000`, `9000`, `9001` или
`9090`. Из внешней сети проверьте redirect HTTP→HTTPS, вход, refresh, logout,
печать чека, импорт каталога и установку PWA.

Проверьте response headers главной страницы:

- `Content-Security-Policy`;
- `X-Content-Type-Options: nosniff`;
- `X-Frame-Options: DENY`;
- `Referrer-Policy`;
- `Permissions-Policy`.

## 7. Обновление и откат

1. Создайте backup и подтвердите возможность восстановления.
2. Соберите новый неизменяемый `AURUM_IMAGE_TAG`.
3. Выполните `db-role-bootstrap`; он идемпотентно нормализует роли и перед
   revision 0067 создаёт недостающие `aurum_migrator`/`aurum_schema_owner`.
4. Выполните `migrate`.
5. Пересоздайте backend, Celery и gateway.
6. Выполните smoke-тесты.

Откат приложения выполняется возвратом предыдущего image tag только при
обратно-совместимой схеме. Production-БД не откатывается миграцией вниз. Если
изменение схемы несовместимо, используется заранее отрепетированный restore.

## 8. Backup и restore drill

Подготовьте отдельный host filesystem. UID `10001` принадлежит непривилегированному
backup-контейнеру:

```bash
sudo install -d -m 700 -o 10001 -g 10001 /srv/aurum-backups/restic
sudo install -d -m 700 -o 10001 -g 10001 /srv/aurum-backups/scratch
sudo install -d -m 750 -o 70 -g 70 /srv/aurum-backups/wal-archive
sudo install -d -m 755 -o root -g root /var/lib/aurum/recovery-metrics
sudo install -d -m 700 -o 10001 -g 10001 /var/lib/aurum/offsite-candidates
sudo install -d -m 700 -o 10001 -g 10001 /var/lib/aurum/offsite-approvals
sudo install -d -m 700 -o 10001 -g 10001 /var/lib/aurum/verified-checkpoints
sudo install -d -m 700 -o 10001 -g 10001 /var/lib/aurum/signing-authorizations
sudo install -d -m 700 -o 10001 -g 10001 /var/lib/aurum/trusted-checkpoints
```

`AURUM_BACKUP_REPOSITORY` хранит зашифрованные snapshots, а
`AURUM_BACKUP_SCRATCH` является очищаемым дисковым рабочим каталогом. Не
размещайте scratch в RAM (`tmpfs`). До запуска убедитесь, что свободного места
не меньше `AURUM_BACKUP_MIN_FREE_BYTES`; для production задайте запас выше
максимального ожидаемого размера БД и MinIO.

Создать зашифрованный combined snapshot PostgreSQL + текущих MinIO-объектов:

```bash
docker compose \
  --env-file /etc/aurum/production.env \
  --file docker-compose.production.yml \
  --file docker-compose.recovery.yml \
  --profile backup run --rm backup
```

Создать и проверить физический base backup, затем зашифровать текущую цепочку
WAL:

```bash
docker compose \
  --env-file /etc/aurum/production.env \
  --file docker-compose.production.yml \
  --file docker-compose.recovery.yml \
  --profile backup run --rm pitr-basebackup

docker compose \
  --env-file /etc/aurum/production.env \
  --file docker-compose.production.yml \
  --file docker-compose.recovery.yml \
  --profile backup run --rm wal-snapshot
```

Перед off-site запуском внешний bucket создаётся в отдельном аккаунте/регионе с
Versioning и default Object Lock `COMPLIANCE`. Ключ uploader получает только
List/Get/Put и не получает Delete, изменение retention или bypass. Затем:

```bash
docker compose \
  --env-file /etc/aurum/production.env \
  --file docker-compose.production.yml \
  --file docker-compose.recovery.yml \
  --profile offsite run --rm offsite-sync
```

Uploader экспортирует только зашифрованные Restic-объекты и не получает
`RESTIC_PASSWORD`. Он создаёт неподписанный candidate в
`AURUM_OFFSITE_CANDIDATE_DIR`: SHA-256, размер и точный WORM `version ID` каждого
объекта. Candidate ещё не считается доверенным.

Выберите конкретный `export ID` из имени `*.candidate.json`. На независимом
recovery-host получите version ID и SHA-256 manifest напрямую из control plane
провайдера, а не из production candidate, и создайте
`$AURUM_OFFSITE_APPROVAL_DIR/$export_id.approval.json` с полями
`schema_version`, `export_id`, `manifest_key`, `manifest_version_id` и
`manifest_sha256`. Значения должны быть получены независимым каналом провайдера;
простое копирование production candidate запрещено.
Verifier не монтирует production candidate. Он использует отдельные read-only
credentials и проверяет все зафиксированные версии и весь Restic repository:

```bash
export AURUM_OFFSITE_EXPORT_ID=20260829T034031Z-1234
docker compose \
  --env-file /etc/aurum/recovery.env \
  --file docker-compose.production.yml \
  --file docker-compose.recovery.yml \
  --profile offsite-trust-verify run --rm offsite-trust-verify
```

После этого оператор сверяет export ID и provider approval, вычисляет SHA-256
перенесённого verified JSON и отдельным каналом записывает только digest в
`$AURUM_SIGNING_AUTHORIZATION_DIR/$export_id.authorize.sha256`. Verifier не имеет
доступа к этому каталогу. Signer не имеет сети и получает закрытый ключ,
verified-каталог, authorization-каталог и каталог публикации:

```bash
docker compose \
  --env-file /etc/aurum/recovery.env \
  --file docker-compose.production.yml \
  --file docker-compose.recovery.yml \
  --profile offsite-trust-sign run --rm offsite-trust-sign
```

Каталог `$AURUM_OFFSITE_EXPORT_ID.trusted` из
`AURUM_TRUSTED_CHECKPOINT_DIR` публикуется атомарно и хранится вне production
host. Для восстановления передайте каталог и публичный ключ на restore-host:

```bash
export AURUM_OFFSITE_EXPORT_ID=20260829T034031Z-1234
docker compose \
  --env-file /etc/aurum/recovery.env \
  --file docker-compose.production.yml \
  --file docker-compose.recovery.yml \
  --profile offsite-restore run --rm offsite-trusted-restore
```

Restore проверяет подпись, bucket/prefix, точные версии, размеры и SHA-256. В
подписанную точку входят combined backup, физическая база PostgreSQL и WAL.
Проверяются dump, объекты MinIO, `pg_verifybackup` и наличие WAL. Команда не
использует `latest`, поэтому новая версия после подписания не меняет выбранную
точку. Это проверка полного payload, а не доказательство service RTO: запуск
приложения и пользовательский smoke выполняются на независимом recovery-host.
Полный протокол зафиксирован в ADR-0026.

После первой ручной проверки установите три пары unit-файлов из
`infra/systemd`: full backup выполняется ежедневно, WAL snapshot и WORM export -
каждые пять минут, изолированный logical/PITR restore drill - ежемесячно. Все
операции сериализуются одним host lock. Скорректируйте `WorkingDirectory`, затем:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now \
  aurum-backup.timer aurum-wal-offsite.timer aurum-restore-drill.timer
systemctl list-timers 'aurum-*'
```

Каждый job атомарно записывает в `AURUM_RECOVERY_METRICS_DIR` время последней
попытки и успеха, статус и длительность. Отдельный непривилегированный exporter
видит только этот каталог read-only через внутреннюю monitoring-сеть. Prometheus
формирует alerts о неуспехе, пропаже метрик, устаревшем
backup/WAL/off-site/drill и превышении предварительного технического порога
restore в 60 минут. Доставка alerts дежурному требует отдельного Alertmanager
receiver и до его настройки не считается закрытой.

Для внеплановой проверки запустите тот же wrapper вручную. Он создаёт уникальный
Compose project, проверяет logical и PITR restore и всегда удаляет временные
volumes:

```bash
sudo AURUM_RECOVERY_METRICS_DIR=/var/lib/aurum/recovery-metrics \
  ./scripts/run-production-restore-drill.sh
```

Wrapper сам создаёт уникальную именованную restore point, дожидается архивации
соответствующего WAL, фиксирует свежий WAL snapshot и проверяет достигнутый LSN.
Поэтому успешный PITR drill не может ограничиться запуском одной base backup.

Локальный drill подтверждает восстановимость repository, но не измеряет полный
service RTO: exact-version WORM payload проверяется отдельно, а service RTO также
включает запуск приложения и пользовательский smoke-тест. Успех содержит
`Restore drill passed`, фактическую Alembic revision и число
объектов, их SHA-256, RLS и доступ runtime-ролей. PostgreSQL dump и текущий
снимок объектов MinIO создаются последовательно, поэтому межсистемная
транзакционная согласованность не гарантируется без окна запрета записей. Этот
backup остаётся логическим и дополняет физический PITR-контур.

PITR всегда репетируется только в пустом scratch. Не запускайте контейнер
`pitr-restore-drill` вручную без пары `AURUM_PITR_TARGET_NAME` и
`AURUM_PITR_TARGET_LSN`: оба значения должны относиться к одной свежей restore
point, а её WAL уже должен входить в зашифрованный snapshot. Штатный безопасный
путь - wrapper выше; пустые значения завершают проверку ошибкой.

Никогда не удаляйте WAL только по возрасту. Очистка разрешена лишь после
подтверждённого off-site manifest и проверенного base backup, который начинается
раньше самого старого сохраняемого момента восстановления.

## 9. Edge shadow rehearsal

Старый `docker-compose.edge.yml` остаётся только dev overlay. Для защищённой
read-only проверки используйте `docker-compose.edge-shadow.hardened.yml` и
`.env.edge-shadow.example`. Генератор секретов требует уже выданный Cloud token:

```powershell
$token = Read-Host "Enrolled Edge credential" -AsSecureString
pwsh ./scripts/New-EdgeShadowSecrets.ps1 `
  -OutputDirectory /etc/aurum-edge/secrets `
  -EdgeCredential $token
```

Этот профиль не является offline POS: writer readiness и activation принудительно
выключены. Устанавливать его как production Edge до mTLS/device PKI запрещено.

## 10. Оставшиеся блокеры

До реального пилота ещё обязательны:

- TLS и проверка сертификатов для PostgreSQL, Redis и MinIO внутри сети;
- внешний WORM bucket в юридически допустимом регионе, независимый recovery-host,
  раздельное хранение ключей и staging drill из выбранного signed checkpoint;
- staging-замеры RPO/RTO на production-подобном объёме и утверждение целевых
  порогов; автоматические freshness/RTO metrics и alerts уже включены;
- HSTS после проверки восстановления TLS на staging;
- подписанные release images, SAST/DAST и внешний penetration test;
- общий rate limit/DDoS-контроль на доверенном edge/WAF.
