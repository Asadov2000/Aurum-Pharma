# Production deployment runbook

Статус: подготовленный single-host контур. Он не разрешён для пилота, пока не
закрыты оставшиеся P0-блокеры в `docs/security-release-plan.md`.

## 1. Требования

- Linux-сервер с Docker Engine и Docker Compose v2.
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

## 4. Проверка конфигурации

Из корня проекта:

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
3. Выполните `migrate`.
4. Пересоздайте backend, Celery и gateway.
5. Выполните smoke-тесты.

Откат приложения выполняется возвратом предыдущего image tag только при
обратно-совместимой схеме. Production-БД не откатывается миграцией вниз. Если
изменение схемы несовместимо, используется заранее отрепетированный restore.

## 8. Оставшиеся блокеры

До реального пилота ещё обязательны:

- TLS и проверка сертификатов для PostgreSQL, Redis и MinIO внутри сети;
- WAL/PITR, off-site backup, MinIO versioning и автоматический restore-test;
- HSTS после проверки восстановления TLS на staging;
- image scan, SBOM, SAST/DAST и внешний penetration test;
- общий rate limit/DDoS-контроль на доверенном edge/WAF.
