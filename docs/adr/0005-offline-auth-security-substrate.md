# ADR-0005: Безопасная основа offline-auth

- Статус: принято
- Дата: 2026-07-15
- Уточняет: ADR-0001, ADR-0004

## Контекст

Касса должна продолжать наличные продажи при потере WAN, но обычные Cloud-секреты
нельзя переносить на Edge. Копия password hash, access/refresh token, TOTP secret
или email-кода позволила бы использовать компрометированный кассовый компьютер
для входа в Cloud или другую аптеку.

Offline-auth также нельзя строить только на системных часах Windows и DPAPI:
часы переводятся назад, а защищённый blob можно восстановить из старой копии.
Положительное решение требует одновременно доказать scope, полномочия кассира,
владение ключом устройства, срок grant и отсутствие rollback состояния.

## Решение

Версия v0 вводит только непубликуемый контракт и fail-closed интерфейсы. Runtime
использует исключительно `DenyAllOfflineAuthVerifier`. Новых routes, таблиц,
writer API и строк `offline_auth` в component ledger нет; профиль
`cash_sale_v1_full_v1` остаётся недоступным.

Cloud grant содержит только:

- точный activation/tenant/branch/Edge/register/writer epoch/user scope;
- capability `cash_sale_v1` и закрытый набор узких POS-команд;
- TPM2 key id и SHA-256 отпечаток DER SubjectPublicKeyInfo;
- монотонные `policy_revision` и `subject_revision`;
- время свежей online-аутентификации, выпуска и абсолютного истечения.

Email, телефон, ФИО, роли, общий список permissions, Cloud session id, пароли,
токены и одноразовые коды в grant запрещены строгой схемой `extra="forbid"`.
Команды не являются permissions и задают только верхнюю границу: будущая
проверка обязана пересечь их с точным authorization snapshot.

Подписываемые байты определены однозначно:

```text
b"aurum:offline-auth-grant:v0\0" + canonical_json_bytes(claims)
```

`claims_hash` равен SHA-256 этих байтов. Подпись - Ed25519 закреплённым Cloud
ключом. Development HMAC, Edge bearer credential и JWT secret не применяются.

Обычный grant выпускается не позднее пяти минут после интерактивной online-
аутентификации и истекает не позднее 72 часов от неё. Ровно в `expires_at`
доступ уже запрещён. Семидневный emergency mode, offline renewal, возвраты и
переключение кассиров в v0 не представлены.

Перед будущим `allow` обязательны все проверки:

1. strict parse, canonical hash и Ed25519 подпись доверенным Cloud key id;
2. полное равенство active writer scope и TPM device binding;
3. существующая локальная сессия того же пользователя;
4. точное равенство policy/subject revisions с authorization snapshot;
5. наличие команды одновременно в grant и authorization snapshot;
6. защищённое монотонное время и непрерывность после перезапуска;
7. атомарный anti-rollback `check_and_advance` перед возвратом `allow`.

Ошибка, отсутствие или повреждение любой зависимости означает общий
`offline_auth_unavailable` без fallback. DPAPI используется только для защиты
at-rest; production требует TPM, mTLS identity, BitLocker, trusted clock и
sealed high-water state. Grant сам не создаёт и не восстанавливает сессию.

Эффективные Cloud-права пока читаются напрямую из PostgreSQL. Межзапросный Redis-
кэш отключён до появления транзакционной authorization revision: удаление ключа
до commit имеет гонку и может временно вернуть уже отозванное право.

## Последствия

Плюсы: формат grant детерминирован и переносим между Cloud и Edge; опасные поля
не могут незаметно попасть в payload; текущий runtime технически не способен
включить offline-auth одним флагом или неполной настройкой.

Цена: v0 ещё не даёт кассиру работать без online-сессии. Следующий положительный
этап возможен только после реализации и аппаратной проверки всех адаптеров,
authorization revisions и защищённого хранения на целевой Windows-конфигурации.
