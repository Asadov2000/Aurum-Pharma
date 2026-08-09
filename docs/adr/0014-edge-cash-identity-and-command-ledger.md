# ADR-0014: Неизменяемая идентичность и журнал Edge cash

- Статус: принят
- Дата: 2026-08-09
- Уточняет: ADR-0002, ADR-0012, ADR-0013

## Контекст

`session_user` безопасен как источник идентичности только при наличии
неизменяемого соответствия между индивидуальной PostgreSQL-ролью и физическим
Edge-узлом. В текущей схеме такого соответствия нет. Существующий `pos_command`
хранит идемпотентность команд облачного черновика и не фиксирует activation,
узел, writer epoch и разрешённую кассу, поэтому он не является финансовым
журналом Edge.

Создание `SECURITY DEFINER` dispatcher до этих двух контрактов позволило бы
доверять переданным UUID или изменяемым `app.*` GUC. Такая функция не может быть
выпущена даже при выключенном HTTP runtime.

## Решение

Миграция `0084` добавляет две tenant-таблицы:

1. `edge_cash_node_identity` связывает ровно одну индивидуальную роль вида
   `aurum_edge_node_<uuid_hex>` с одним Edge-узлом, кассой и tenant/branch scope.
   Привязка хранит одновременно имя роли и её PostgreSQL OID. Будущий dispatcher
   обязан сверять и имя, и OID, поэтому
   удаление и повторное создание роли с тем же именем не переносит доверие.
   Вставка привязки уже сейчас отклоняется, если роль не существует, пара
   name/OID расходится, LOGIN-атрибуты небезопасны либо роль имеет любое членство,
   кроме наследуемого `aurum_edge_cash_executor` без `ADMIN` и `SET ROLE`.
2. `edge_cash_command` хранит завершённую денежную команду
   `sale.cash.complete`, её canonical request hash и неизменяемый result snapshot.
   Составные внешние ключи фиксируют identity, activation, writer epoch, узел,
   кассу и завершённую продажу в одной области. Связь с продажей дополнительно
   доказывает совпадение кассира, `operation_id`, номера чека, суммы и валюты;
   JSON result обязан повторять эти значения. Одна продажа может иметь только
   одну запись Edge cash.

Обе таблицы используют `FORCE ROW LEVEL SECURITY`, не имеют RLS policies и не
выдают прямых прав `PUBLIC`, `aurum_app`, `aurum_support` или Edge capability-
ролям. До следующей миграции они доступны только владельцу схемы и остаются
пустыми. `UPDATE` и `DELETE` блокируются триггером. Audit для денежной команды
содержит только идентификаторы и SHA-256 hashes; result payload в audit-log не
копируется.

Повтор по `(tenant_id, operation_id)` в будущем обязан вернуть сохранённый
`result_payload` только при совпадении `request_hash`. Другой hash является
конфликтом, а не новой операцией.
Dispatcher также обязан брать общий POS advisory lock и проверять `operation_id`
во всех облачных и Edge-журналах, чтобы одна операция не могла одновременно
появиться в `pos_command` и `edge_cash_command`.

## Граница выпуска

Этот этап не создаёт LOGIN-роли, provisioning API, dispatcher, route или
положительный offline-auth verifier. `aurum_edge_cash_executor` сохраняет нулевой
ACL, а runtime продолжает использовать `DenyAllOfflineAuthVerifier`.

Следующая миграция может добавить dispatcher только одновременно с:

- защищённым provisioning индивидуальной LOGIN-роли и identity binding;
- allowlist в cluster role contract;
- тестами через реальный `session_user`, без `SET ROLE` и доверия к GUC;
- полной атомарностью sale, FEFO, stock movement, cash payment, receipt, audit,
  outbox и `edge_cash_command`;
- fail-closed downgrade при наличии хотя бы одной identity-привязки или
  финансовой команды.
