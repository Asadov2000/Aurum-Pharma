# ADR-0008: Production-периметр на Caddy и Docker secrets

- Статус: принято
- Дата: 2026-07-23
- Заменяет предварительный выбор nginx из `docs/spec-v3.md`

## Контекст

Development и demo-конфигурации запускают Vite, монтируют исходники и публикуют
служебные порты. Они не могут быть основой staging или production.

Первый production-контур должен работать на одном Linux-сервере, автоматически
обновлять TLS-сертификат, отдавать SPA и API с одного origin и не требовать
отдельного Certbot. Ошибка продления сертификата или передача секрета через
Compose environment создают неприемлемый риск для пилота.

## Решение

1. Единственной публичной точкой является официальный образ Caddy. Он принимает
   `80/443`, выполняет HTTP-to-HTTPS redirect, отдаёт собранный frontend и
   проксирует только `/api/*` и `/healthz` в backend.
2. Caddy и backend работают от UID `10001`, с read-only root filesystem,
   `no-new-privileges` и удалёнными Linux capabilities. Внутри контейнера Caddy
   слушает непривилегированные порты `8080/8443`.
3. PostgreSQL, Redis, MinIO, backend и Prometheus не публикуют host-порты.
   Отдельные `internal` сети разделяют proxy, данные и мониторинг.
4. Backend доверяет forwarded headers только фиксированному IP Caddy и принимает
   только точный production Host. OpenAPI отключён вне development.
5. Секреты передаются как файлы Docker secrets в `/run/secrets`. `.env` содержит
   только несекретные параметры. PostgreSQL root, runtime DB roles, Redis, JWT,
   MFA, metrics, SMTP и MinIO используют независимые значения.
6. Backend использует отдельного MinIO-пользователя с доступом только к bucket
   `aurum`; root credentials не выдаются приложению.
7. SPA получает enforcing CSP и остальные browser security headers на Caddy.
   Inline-скрипт выбора темы вынесен в статический файл. Inline styles временно
   требуют `style-src 'unsafe-inline'`.

## Rate limiting

Стандартный официальный Caddy не содержит rate-limit модуля. Сторонний модуль не
добавляется, чтобы не расширять supply-chain без отдельного review. Текущие
auth/OTP ограничения остаются в приложении и Redis. До публичного пилота общий
rate limit и DDoS-защита должны быть включены на доверенном edge/WAF; его proxy
CIDR добавляется только после отдельной настройки trusted proxies.

## Ограничения

Этот ADR закрывает внешний периметр и доставку секретов, но не является
разрешением релиза. Внутренний TLS для PostgreSQL/Redis/MinIO, backup с WAL/PITR,
restore-test, image scan/SBOM и внешний penetration test остаются отдельными
блокерами из `docs/security-release-plan.md`.

HSTS не включается до проверки TLS и процедуры восстановления сертификата на
staging. После проверки включается без `preload`; `includeSubDomains` допустим
только после аудита всех поддоменов.
