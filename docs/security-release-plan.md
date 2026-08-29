# Security Release Plan

Дата ревизии: 2026-07-23.

> Этот checklist содержит исторические этапы hardening и пока не пересверен
> построчно с миграциями после `0066`. Для текущего проверенного состояния,
> открытых рисков и порядка работ используйте
> [`development-state.md`](development-state.md). Пункты ниже нельзя считать
> незавершенными только потому, что здесь остался пустой checkbox.

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
- [x] Зашифрованный logical backup PostgreSQL + MinIO, отдельные read-only
      credentials, retention и изолированный restore drill реализованы и локально
      проверены на полной схеме и повторным тестом владельцев, ACL и RLS.
- [x] WAL archive, проверяемый physical base backup, PITR drill и независимый
      off-site WORM export с append-only credentials реализованы в коде и CI.
- [x] WAL/off-site запускаются каждые пять минут, restore drill - ежемесячно;
      Prometheus контролирует статус, freshness и длительность recovery jobs.
- [x] Provider-approved Ed25519 checkpoint фиксирует точные WORM version IDs;
      online verifier, offline signer без сети, read-only restore credentials и
      отдельный операторский authorization digest проверяются в CI. Fail-closed
      restore отклоняет подмену manifest и игнорирует новую версию после подписи.
- [ ] Production bucket создан в разрешённой юрисдикции; настроены внешний
      recovery-host, уведомления о free-space и измерены RPO/RTO на
      production-подобном объёме.

### P0: identity и authorization

- [x] Глобальный account отделён от tenant membership, защищённого ownership и
      обычных рабочих ролей; обязательные контракты ADR-0007 закрыты.
- [x] Передача владения требует recent MFA у действующего и нового владельца,
      атомарно меняет protected ownership/role, отзывает сессии и записывает
      неизменяемый audit; запрос видят только его участники.
- [x] Убран общий bypass permissions для Aurum Administrator; Developer-only,
      platform, tenant и branch capabilities проверяются раздельно.
- [x] Tenant-пользователю запрещено создавать или прикреплять глобальный account;
      владелец назначает роли только заранее созданным membership своего tenant.
- [x] Конструктор получает с сервера только delegable capabilities текущего
      пользователя и scope; шаблон и прямой API-запрос не обходят этот каталог.
- [x] Роли публикуются неизменяемыми версиями; assignment закреплен за
      опубликованным снимком, stale publication отклоняется, а архивирование
      активной роли требует явную замену.
- [x] Запрещены self-assignment, изменение собственной границы полномочий,
      назначение protected-ролей и удаление последнего владельца.
- [x] TOTP для support-уровней 1–2, защищённое восстановление и аудит recovery.
- [x] Self-service session inventory и ручной отзыв своих активных сеансов с
      немедленным прекращением доступа.
- [x] Административный принудительный logout пользователя с tenant-ограничением,
      защитой владельца и иммутабельным аудитом.
- [x] Обязательное in-app предупреждение о входе из нового браузера или
      приложения: случайный device ID хранится только в HttpOnly cookie, в БД
      записывается SHA-256; смена IP не вызывает ложное предупреждение.
- [x] Все `BRANCH_SET` permissions синхронизированы с fail-closed реестром
      маршрутов. Новый branch-маршрут обязан объявить стратегию проверки scope,
      пустой scope отклоняется, а CI проверяет полноту контракта.
- [x] Уведомления о лицензии филиала получают только активные владельцы аптеки;
      обычный сотрудник не получает операционные данные чужого филиала.
- [x] Изменения membership, ownership, role capabilities, assignments и support
      sessions записываются в неизменяемый аудит с точным before/after diff.
- [x] Доступ support к tenant выполняется только через короткую support session
      с причиной, явным scope и step-up MFA.
- [x] Trusted-proxy allowlist для client IP; forwarded headers принимаются
      только от фиксированного адреса Caddy.

### P0: деньги, рецепты и бизнес-целостность

- [x] DB-level запрет изменения/удаления completed sale, её позиций, оплат и
      рецептурных записей; возврат только новой связанной операцией без изменения
      исходного чека.
- [x] Продажа просроченной партии безусловно запрещена; срок повторно проверяется
      при завершении продажи, а устаревший флаг подтверждения не ослабляет правило.
- [ ] `refund_reason_mode` реально требует допустимую причину/комментарий.
- [x] Refund/void lifecycle входит в sync-контур через неизменяемое событие
      `pos.sale.refunded.v1`, связь с исходной продажей и вычисляемый признак
      полного возврата. Сама offline-команда возврата по-прежнему запрещена
      согласно ADR-0001 до реализации денежной reconciliation.
- [ ] Идемпотентность, конкурентные продажи, остатки, деньги `NUMERIC(14,2)` и
      сверка с фискальным/эквайринговым оператором покрыты тестами.

## P1 после закрытия P0

- [x] Base images закреплены по digest; CI выполняет Dockerfile/secret/image scan
      и сохраняет CycloneDX SBOM для трёх production images.
- [ ] Release images публикуются по digest в registry и подписываются Cosign через
      OIDC; подпись проверяется перед deployment.
- [ ] Enforcing CSP и Permissions-Policy установлены на production Caddy;
      inline theme script вынесен во внешний файл. HSTS включается только после
      staging-проверки восстановления TLS.
- [ ] Логи используют allowlist и redaction; в логах/трейсах нет cookies,
      Authorization, email пациентов, рецептов и закупочных цен.
- [ ] Prometheus получает token через secret file, порт 9090 закрыт извне и
      availability/recovery freshness/restore-duration alerts добавлены; TLS
      expiry и внешний Alertmanager-канал доставки ещё не настроены. Полный
      service RTO фиксируется только staging drill согласно ADR-0025.
- [ ] Windows-клиент выпускается как подписанный MSIX/AppInstaller с
      проверкой publisher, anti-rollback и безопасным WebView2 allowlist.
- [ ] SAST/DAST, ручной threat model и внешний penetration test до пилота.

## Порядок следующей разработки

1. Развернуть внешний WORM/recovery-host и измерить RPO/RTO на staging.
2. DB-инварианты POS и полный refund/void sync-контур.
3. Внутренний TLS для PostgreSQL, Redis и MinIO.
4. mTLS/device identity, зашифрованная локальная БД и trusted time для Edge.
5. Подписанный Windows-дистрибутив и безопасное обновление.

До завершения этих пунктов локальный `docker-compose.yml` является только demo
окружением. Его опубликованные порты, dev-серверы и тестовые секреты нельзя
использовать для staging или production.
