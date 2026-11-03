# Источник защищённых данных допуска кассира

`WindowsTpmCashierStateSource` читает существующий файл полного snapshot
допуска и сверяет его с независимым
[TPM checkpoint](windows-tpm-protected-state.md). Он предоставляет
проверенные данные на момент чтения. Это не результат входа, не command
principal и не реализация `TrustedLocalLoginStateProviderV1`.
Runtime автономного входа и продаж остаётся DenyAll.

## Защищаемые данные

Формат `protected_cashier_state.py` и native parser описывают один snapshot:

- общий tenant/branch/node/register/activation/writer scope и TPM device binding;
- независимо закреплённый checkpoint context;
- принятый policy floor: последовательность доставки, claims hash и полная
  tenant policy;
- последнее защищённое наблюдение времени;
- по каждому кассиру: полный authorization, credential context и guard;
- полную текущую сессию рабочего места либо явный `null`.

Секрет, pepper и bcrypt verifier сюда не включены. Credential context связывает
допуск с нужным credential и раздельными device/protection keys, но не
подтверждает целостность самого verifier или пригодность пароля.

Все записи привязаны к общему scope/device. Guard должен совпасть с контекстом
и его существующим canonical digest. Сессия связывается с кассиром по полному
scope, lease, credential/reference revision, reauthentication policy и shift.
Изменение только session ID или её других полей меняет полный snapshot digest;
неизменённый guard не позволяет скрыть такое восстановление.

Новая запрещающая policy может иметь более новую версию без нового lease.
Поэтому старый authorization допустим как сохранённые данные, но
`ReadCashierState` не выбирает его при несовпадении с текущим policy floor.
Истёкшая сессия также может оставаться данными; это не продлевает её действие.

## Чтение и граница доверия

`OpenExisting` требует заранее подготовленные index, checkpoint и snapshot,
а также независимо доверенные ожидаемые scope, device и context. Вызов не
создаёт enrollment, не принимает policy и не переводит SQL inbox в доверенное
состояние. Подписи и происхождение данных обязан проверить доверенный owner
до первоначального provisioning или обновления защищённого состояния.

Чтение удерживает исключительный дескриптор snapshot до завершения. Parser
проверяет строгую структуру, затем source сверяет hash точных байтов с
checkpoint и повторно читает checkpoint, требуя те же поколение и digest.
Результат выходит только после закрытия файла. Подмена, откат, изменение
checkpoint, ошибка чтения или закрытия дают общий отказ и блокируют экземпляр.
Нет кэша старого snapshot, автоматической инициализации или восстановления.

`ReadCashierState` выбирает только активную запись текущего policy floor и
допустимого tenant lifecycle. Tenant policy может лишь ограничить сохранённые
команды; если набор противоречит policy на момент сохранённого наблюдения,
кассир не выбирается. Обычный отказ выбора не делает неповреждённый snapshot
непригодным для отдельного чтения.

Сохранённое время служит исторической проверкой данных, не свежим UTC.
Свежие сроки lease/session, `guard.blocked_until`, проверка пароля и
аппаратной непрерывности, доставка отзыва и сериализация до SQL commit
остаются обязанностью будущего доверенного provider. DTO не даёт права
выполнить команду и не заменяет эти проверки.

## Совместимость и проверки

Raw UTF-8 ограничен 1 МиБ, JSON — глубиной 32. Дубликаты, неизвестные поля,
booleans, float/exponent, некорректный Unicode, UUID не v4 D и timestamps вне
UTC ISO seconds с точностью до микросекунд отклоняются. Явные nullable-поля
обязательны. Cashier records уникальны и отсортированы.

Python encoder создаёт canonical JSON. Обе стороны хешируют
`aurum:protected-cashier-state:v1\0` и **точные исходные байты**;
при проверке hash нормализация и повторная сериализация не выполняются.
Credential context использует отдельный уже существующий canonical contract.
Общая полностью искусственная фикстура находится в
`docs/fixtures/protected-cashier-state-v1.json` и `.sha256`.

Native protocol и реальные файловые тесты используют тестовый TPM transport,
не обращаются к аппаратуре или общей БД. Реальный защищённый credential/time
provider, служба/IPC/ACL, приём policy, recovery и аппаратные испытания ещё
не завершены. E6-06 остаётся открыт: **2/7 (4)**.
