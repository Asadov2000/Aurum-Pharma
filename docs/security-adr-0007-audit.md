# Аудит реализации ADR-0007

- Дата: 2026-08-29
- Проверенный baseline: реализация immutable role publication в ветке
  `codex/immutable-role-publication`
- Область: platform account, tenant membership/ownership, роли, назначения,
  branch scope, support access и аудит

## Резюме

Основная модель ADR-0007 уже работает и защищена не только frontend-проверками,
но также сервисным слоем, RLS, DB-триггерами и isolation-тестами. Подтвержденного
критического обхода tenant isolation или конструктора ролей в проверенном контуре
не обнаружено.

До полного выполнения ADR остается один самостоятельный этап: исчерпывающая
матрица branch-scope проверок для каждого доменного маршрута. Неизменяемые
версии опубликованных ролей реализованы. Устаревшие числовые `level` сохранены
только для совместимости и отображения; источником решения о доступе служит
scoped authorization snapshot.

## Подтверждено кодом и тестами

1. Глобальный `app_user` отделен от `tenant_membership` и
   `tenant_ownership`. Tenant workflow не создает глобальный аккаунт и ищет
   membership только внутри текущего tenant
   (`backend/app/domains/roles/service.py:216`,
   `backend/app/domains/roles/service.py:792`).
2. Platform-кандидат не получает Developer/Administrator capability при одном
   приглашении. Platform grant имеет отдельный защищенный lifecycle и второе
   подтверждение при наличии другого Developer
   (`backend/app/domains/platform_accounts/service.py:69`,
   `backend/app/domains/platform_access/service.py:105`).
3. Конструктор и шаблоны проходят через один delegation envelope. Скрытые,
   platform, protected и неподходящие по scope capabilities отклоняются сервером
   (`backend/app/domains/roles/service.py:143`,
   `backend/app/domains/roles/service.py:185`).
4. Self-assignment, изменение собственной роли, обычное назначение владельцу и
   изменение последнего активного владельца запрещены сервисом и DB guard
   (`backend/app/domains/roles/service.py:407`,
   `backend/app/domains/roles/service.py:647`,
   `backend/app/domains/roles/service.py:762`).
5. Capability сохраняет scope конкретного назначения. Основные branch-домены
   передают разрешенный scope в service/repository, а POS дополнительно разделяет
   own, tenant-view и manage scopes
   (`backend/app/core/deps.py:365`,
   `backend/tests/domains/security/test_authz_gates.py:512`).
6. Изменения assignment и policy раздельно увеличивают subject/policy revision;
   Redis не используется как источник авторизации
   (`backend/app/domains/roles/repository.py:50`,
   `backend/tests/domains/roles/test_redis_cache.py:13`).
7. Support-доступ ограничен tenant, сроком, причиной и явным каталогом
   capabilities. Tenant-запросы выполняются через scoped support session, а не
   по одному факту подключения к privileged DB pool
   (`backend/app/domains/support_access/service.py:53`,
   `backend/app/core/deps.py:116`).
8. Membership, ownership, role permission diff, assignment и support lifecycle
   попадают в иммутабельный audit log. Для роли сохраняются точные
   `before_permissions` и `after_permissions`
   (`backend/alembic/versions/0053_add_scoped_delegated_authorization.py:1337`,
   `backend/alembic/versions/0053_add_scoped_delegated_authorization.py:1453`).
9. Передача владения выполняется защищенной DB-командой: действующий владелец
   создаёт запрос после recent MFA, целевой участник подтверждает его после
   собственной recent MFA, а смена ownership, protected-роли, revisions, сессий
   и audit фиксируется одной транзакцией. RLS раскрывает запрос только его
   участникам (`backend/alembic/versions/0116_add_ownership_transfer.py`,
   `backend/tests/isolation/test_ownership_transfer.py`).

10. Роль публикуется immutable-версиями. Активное назначение закреплено за
    опубликованным снимком; новая публикация атомарно архивирует предыдущий
    снимок, обновляет назначения и revisions. Архивирование с активными
    назначениями требует явную опубликованную замену
    (`backend/alembic/versions/0117_add_immutable_role_versions.py`,
    `backend/tests/domains/roles/test_role_versions.py`).

11. Каждый маршрут с `BRANCH_SET` capability использует явную стратегию
    branch-gate: прямую проверку `branch_id`, SQL-фильтрацию коллекции, проверку
    загруженного ресурса или ограниченный tenant-справочник. Пустой scope
    отклоняется, а реестр permission codes сверяется с БД
    (`backend/app/core/deps.py`,
    `backend/tests/core/test_branch_route_contract.py`).

## Закрытые разрывы

### AUTHZ-002: неизменяемые публикации ролей

- Статус: закрыт 2026-08-29.
- Реализованы `access_role_version`, immutable capability snapshots,
  optimistic publication, assignment pinning, история версий и атомарный
  archive-with-replacement workflow.
- Подтверждено domain-, isolation- и frontend-тестами, полным backend-набором и
  циклом миграции `0117 -> 0116 -> 0117` на одноразовой БД.

### AUTHZ-003: полный fail-closed branch route gate

- Статус: закрыт 2026-08-29.
- Все активные `BRANCH_SET` permissions входят в синхронизируемый с БД runtime
  реестр. Маршрут с таким permission без branch policy ломает CI.
- POS, партии, приходы, возвраты поставщику, филиалы, кассы, отчеты и смешанные
  discovery-маршруты переведены на явные gate dependencies. Missing/empty scope
  отклоняется до выполнения доменной операции.
- Двухфилиальная HTTP-матрица и E2E кассира подтверждают, что capability филиала
  A не раскрывает и не изменяет ресурсы филиала B.
- Обнаруженная аудитом рассылка сведений о лицензии всем tenant-пользователям
  ограничена активным владельцем и покрыта отдельным тестом получателей.

## Оставшиеся разрывы

Критических разрывов обязательного контракта ADR-0007 не осталось. Legacy-поля
сохраняются только для совместимости и удаляются отдельной миграцией.

## Совместимость

Поля `role.level`, `AppUser.is_developer` и `is_administrator` пока нельзя
удалять одним изменением: они участвуют в совместимом login/UI-контракте.
Авторизация уже использует DB-backed platform capabilities, ownership и scoped
snapshot. Удаление legacy-полей выполняется отдельной миграцией после обновления
всех клиентов и токенов.
