# Security Release Plan

Дата ревизии: 2026-07-17.

Абсолютно неуязвимого приложения не бывает. Цель Aurum Pharma — уменьшить
вероятность инцидента, ограничить его последствия и иметь проверенное
восстановление. Для проверки веб-части используем OWASP Top 10 как минимум
осведомлённости, а требования к релизу сверяем с [OWASP ASVS](https://owasp.org/www-project-application-security-verification-standard/).

## Уже реализовано

- PostgreSQL RLS и разделение ролей `aurum_app` / `aurum_support` для tenant-изоляции.
- Access-токен живёт только в памяти frontend; refresh-токен передаётся только в
  `httpOnly` cookie, ротируется и хранится в БД только как hash.
- Refresh/logout отклоняют refresh-токен в JSON-теле; origin для cookie-операций
  проверяется явно.
- Назначение, повторная активация и отзыв ролей ограничены филиалом актёра на
  уровне service-policy и PostgreSQL trigger.
- `/metrics` закрыт bearer-секретом вне development; секрет обязателен для
  staging/production и не попадает в представление настроек.
- CI фиксирует lock-файлы, запускает Python/JS dependency audit и теперь падает,
  если advisory-сервис недоступен.
- Offline-auth работает только в fail-closed deny-only режиме. Полный offline POS
  не считается включённым до появления аппаратной идентификации и доверенного
  времени.

## Блокеры релиза пилота

### P0: инфраструктура и данные

- [ ] Production-образы без dev-серверов и `--reload`; запуск от non-root.
- [ ] TLS на внешнем контуре; PostgreSQL, Redis, MinIO и Prometheus не публикуются
      на host-интерфейс.
- [ ] Секреты выдаются через secret manager/Docker secrets, а не через fallback в
      compose или committed `.env`.
- [ ] Production/staging fail-closed: HTTPS для CORS origins, secure cookies,
      encrypted Redis/MinIO/DB transport и отсутствие default credentials.
- [ ] PostgreSQL backup с WAL/PITR, версионирование MinIO, шифрование,
      off-site-копия, retention и регулярный restore-test с измеренными RPO/RTO.

### P0: identity и authorization

- [ ] TOTP для support-уровней 1–2, защищённое восстановление и аудит recovery.
- [ ] Session inventory, принудительный logout, rotation/revocation и уведомление
      о подозрительном входе.
- [ ] Проверить branch-scoped permissions во всех доменах, не только в roles;
      permission должен действовать только в филиале, где выдана роль.
- [ ] Trusted-proxy allowlist для client IP; не принимать произвольный
      `X-Forwarded-For`.

### P0: деньги, рецепты и бизнес-целостность

- [ ] DB-level запрет изменения/удаления completed sale, её позиций и оплат;
      возврат только новой связанной операцией.
- [ ] `warning` для просроченной партии требует подтверждения с причиной и
      повторной проверки срока при завершении продажи.
- [ ] `refund_reason_mode` реально требует допустимую причину/комментарий.
- [ ] Refund/void events входят в sync-контур до включения любого Edge writer.
- [ ] Идемпотентность, конкурентные продажи, остатки, деньги `NUMERIC(14,2)` и
      сверка с фискальным/эквайринговым оператором покрыты тестами.

## P1 после закрытия P0

- [ ] Образы закреплены по digest, генерируется SBOM и выполняется image scan.
- [ ] Enforcing CSP, HSTS и Permissions-Policy устанавливаются на production
      reverse proxy для SPA; внешние fonts/scripts убраны или разрешены nonce/hash.
- [ ] Логи используют allowlist и redaction; в логах/трейсах нет cookies,
      Authorization, email пациентов, рецептов и закупочных цен.
- [ ] Prometheus получает token через secret file, порт 9090 закрыт извне,
      добавлены alerts на доступность, backup и истечение сертификатов.
- [ ] Windows-клиент выпускается как подписанный MSIX/AppInstaller с
      проверкой publisher, anti-rollback и безопасным WebView2 allowlist.
- [ ] SAST/DAST, ручной threat model и внешний penetration test до пилота.

## Порядок следующей разработки

1. TOTP и защищённый поток восстановления support-аккаунта.
2. Отдельный production compose/reverse-proxy профиль и секреты.
3. Backup/restore job с одноразовой проверкой восстановления.
4. DB-инварианты POS и полный refund/void sync-контур.
5. mTLS/device identity, зашифрованная локальная БД и trusted time для Edge.
6. Подписанный Windows-дистрибутив и безопасное обновление.

До завершения этих пунктов локальный `docker-compose.yml` является только demo
окружением. Его опубликованные порты, dev-серверы и тестовые секреты нельзя
использовать для staging или production.
