# Security Release Plan

Дата ревизии: 2026-07-23.

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
  если advisory-сервис недоступен. Alembic-цепочка проверяется на одноразовой CI
  БД как `upgrade head -> downgrade 0055 -> upgrade head`.
- Offline-auth работает только в fail-closed deny-only режиме. Полный offline POS
  не считается включённым до появления аппаратной идентификации и доверенного
  времени.
- Support-аккаунты уровней 1–2 требуют TOTP после email-кода и пароля. Секрет
  хранится отдельно от `app_user` только в зашифрованном виде; повторное
  использование одного временного кода отклоняется.
- Для активного и ожидающего TOTP-секрета хранятся отдельные версии ключа.
  Версионированный keyring читает переходный набор ключей, а support-only
  операция ротации перешифровывает обе фазы фактора.
- Recovery-коды одноразовые, содержат 96 случайных бит и хранятся только как
  доменно-разделённый SHA-256 digest без зависимости от JWT или ключа шифрования
  TOTP. Восстановление отзывает активные сессии, требует зарегистрировать новый
  TOTP-фактор и фиксируется в неизменяемом аудите без секретов и кодов.
- JWT support-аккаунта привязан к серверной сессии. Её отзыв действует сразу, а
  опасные support-операции требуют недавний step-up MFA. Результат step-up
  записывается только в новый короткоживущий access-токен и не повышает уровень
  refresh-сессии; исходный запрос повторяется только после подтверждения.
- MFA-таблицы закрыты FORCE RLS и недоступны напрямую роли `aurum_app`.
  SECURITY DEFINER-функции с доступом к секрету или изменением MFA-состояния
  принадлежат `aurum_support`, имеют фиксированный `search_path`; EXECUTE для
  `PUBLIC` и `aurum_app` отозван.

## Блокеры релиза пилота

### P0: инфраструктура и данные

- [x] Production-образы без dev-серверов и `--reload`; backend и Caddy работают
      от non-root с read-only root filesystem и удалёнными capabilities.
- [x] TLS на внешнем контуре; PostgreSQL, Redis, MinIO и Prometheus не публикуются
      на host-интерфейс.
- [x] Секреты выдаются через Docker secrets из внешнего защищённого каталога, а
      не через fallback в compose или committed `.env`; MinIO runtime account
      отделён от root.
- [x] Реализованы отдельные active/pending key versions, переходный keyring,
      идемпотентная support-only операция перешифрования и operator runbook.
- [ ] Независимый `MFA_ENCRYPTION_KEY` выдан в secret manager каждой
      staging/production-среды до первого support-enrollment; ротация отрепетирована
      на staging с проверенным backup и rollback.
- [ ] Production/staging fail-closed: HTTPS для CORS origins, secure cookies,
      trusted Host/proxy и отсутствие default credentials уже enforced;
      encrypted Redis/MinIO/DB transport ещё не реализован.
- [ ] PostgreSQL backup с WAL/PITR, версионирование MinIO, шифрование,
      off-site-копия, retention и регулярный restore-test с измеренными RPO/RTO.

### P0: identity и authorization

- [ ] Реализовать ADR-0007: отделить глобальный account от tenant membership,
      защищённого ownership и обычных рабочих ролей.
- [ ] Убрать общий bypass permissions для Aurum Administrator; Developer-only,
      platform, tenant и branch capabilities проверяются раздельно.
- [ ] Запретить tenant-пользователю создавать или прикреплять глобальный account;
      владелец назначает роли только заранее созданным membership своего tenant.
- [ ] Конструктор получает с сервера только delegable capabilities текущего
      пользователя и scope; шаблон и прямой API-запрос не обходят этот каталог.
- [ ] Запретить self-assignment, изменение собственной границы полномочий,
      назначение protected-ролей и удаление последнего владельца.
- [x] TOTP для support-уровней 1–2, защищённое восстановление и аудит recovery.
- [x] Self-service session inventory и ручной отзыв своих активных сеансов с
      немедленным прекращением доступа.
- [x] Административный принудительный logout пользователя с tenant-ограничением,
      защитой владельца и иммутабельным аудитом.
- [x] Обязательное in-app предупреждение о входе из нового браузера или
      приложения: случайный device ID хранится только в HttpOnly cookie, в БД
      записывается SHA-256; смена IP не вызывает ложное предупреждение.
- [ ] Проверить branch-scoped permissions во всех доменах, не только в roles;
      permission должен действовать только в филиале, где выдана роль.
- [ ] Изменения membership, ownership, role capabilities, assignments и support
      sessions записываются в неизменяемый аудит с точным before/after diff.
- [ ] Доступ support к tenant выполняется только через короткую support session
      с причиной, явным scope и step-up MFA.
- [x] Trusted-proxy allowlist для client IP; forwarded headers принимаются
      только от фиксированного адреса Caddy.

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
- [ ] Enforcing CSP и Permissions-Policy установлены на production Caddy;
      inline theme script вынесен во внешний файл. HSTS включается только после
      staging-проверки восстановления TLS.
- [ ] Логи используют allowlist и redaction; в логах/трейсах нет cookies,
      Authorization, email пациентов, рецептов и закупочных цен.
- [ ] Prometheus получает token через secret file, порт 9090 закрыт извне,
      добавлены alerts на доступность, backup и истечение сертификатов.
- [ ] Windows-клиент выпускается как подписанный MSIX/AppInstaller с
      проверкой publisher, anti-rollback и безопасным WebView2 allowlist.
- [ ] SAST/DAST, ручной threat model и внешний penetration test до пилота.

## Порядок следующей разработки

1. Scoped authorization, account/membership/ownership и безопасный конструктор
   из ADR-0007.
2. Backup/restore job с одноразовой проверкой восстановления.
3. DB-инварианты POS и полный refund/void sync-контур.
4. Внутренний TLS для PostgreSQL, Redis и MinIO.
5. mTLS/device identity, зашифрованная локальная БД и trusted time для Edge.
6. Подписанный Windows-дистрибутив и безопасное обновление.

До завершения этих пунктов локальный `docker-compose.yml` является только demo
окружением. Его опубликованные порты, dev-серверы и тестовые секреты нельзя
использовать для staging или production.
