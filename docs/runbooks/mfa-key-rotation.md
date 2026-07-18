# Ротация ключа шифрования support-MFA

Этот runbook описывает подготовленную процедуру. Он не означает, что production
secret manager, ключи или deployment уже настроены.

Процедура перешифровывает активные и ожидающие TOTP-секреты. Recovery-коды
содержат 96 случайных бит и хранятся как SHA-256 digest, независимый от JWT и
ключей шифрования TOTP, поэтому эта ротация их не инвалидирует.

## Предварительные условия

1. Создать и проверить восстановление актуального backup PostgreSQL.
2. Выбрать следующую целую версию ключа.
3. Создать независимый случайный корневой секрет длиной не менее 32 символов в
   secret manager целевой среды.
4. Убедиться, что старый корневой секрет также доступен. Не передавать секреты
   через аргументы командной строки и не выводить их в логи.
5. Согласовать окно изменения и rollback до удаления старой версии.

## Процедура

1. Развернуть backend с новым текущим ключом и старой версией в переходном
   keyring:

   ```dotenv
   MFA_ENCRYPTION_KEY=<new root from secret manager>
   MFA_ENCRYPTION_KEY_VERSION=2
   MFA_ENCRYPTION_PREVIOUS_KEYS={"1":"<old root from secret manager>"}
   ```

   Текущая версия не должна одновременно присутствовать в
   `MFA_ENCRYPTION_PREVIOUS_KEYS`.

2. Пока доступны обе версии, проверить вход существующего support-аккаунта,
   начало recovery и step-up MFA.
3. В одном backend-контейнере выполнить:

   ```powershell
   poetry run python -m app.maintenance.rotate_mfa_key --from-version 1
   ```

   Команда использует текущую версию из `MFA_ENCRYPTION_KEY_VERSION`,
   перешифровывает все active/pending ciphertext со старой версии и выводит
   только число изменённых аккаунтов.

4. Повторить ту же команду. Она должна сообщить
   `Rotated MFA encryption for 0 support account(s).`: операция идемпотентна.
5. Повторно проверить support login, recovery start и step-up MFA.
6. Только после успешных проверок удалить версию `1` из
   `MFA_ENCRYPTION_PREVIOUS_KEYS` и снова развернуть backend.

## Rollback

До шага 6 вернуть старый корневой секрет как текущий, указать версию `1`,
оставить новый корень как предыдущую версию `2` и выполнить:

```powershell
poetry run python -m app.maintenance.rotate_mfa_key --from-version 2
```

После шага 6 сначала вернуть обе версии в keyring. Восстановление только БД без
соответствующих версий из secret manager сделает TOTP-секреты нечитаемыми.

## Граница прав

`rotate_support_mfa_encryption` является `SECURITY DEFINER`-функцией с
фиксированным `search_path`. Владелец и единственная runtime-роль с `EXECUTE` —
`aurum_support`; права `PUBLIC` и `aurum_app` отозваны. Приложение передаёт ключи
только как скрытые bound-параметры, а не как SQL-текст или аргументы CLI.

Для каждого изменённого support-аккаунта создаётся неизменяемое audit-событие
только со старым и новым номерами версий, без ключей и TOTP-секретов.
