# ADR-0013: Граница выполнения Edge cash sale в PostgreSQL

- Статус: принят
- Дата: 2026-08-09
- Уточняет: ADR-0002, ADR-0009, ADR-0012

## Контекст

Обычная роль `aurum_app` имеет широкую поверхность tenant-операций и получает
scope через изменяемые параметры PostgreSQL-сессии. Этого достаточно для Cloud
runtime под RLS, но недостаточно для финансовой команды от физического Edge-узла:
владелец соединения может самостоятельно изменить `app.edge_node_id` и другие
GUC. Выдача этой роли Edge-сервису сделала бы проверку writer scope обходной.

## Решение

Граница Edge cash sale разделяется на три идентичности:

1. Для каждого физического Edge-узла создаётся отдельная `LOGIN`-роль. Она не
   хранится в миграциях и создаётся только защищённым enrollment/bootstrap.
2. `aurum_edge_cash_executor` является `NOLOGIN`, `NOBYPASSRLS` capability-ролью.
   Узловая роль наследует от неё только `CONNECT`, `USAGE` закрытой схемы и
   `EXECUTE` одной функции dispatcher, без права `SET ROLE`.
3. `aurum_edge_cash_owner` является `NOLOGIN`, `NOBYPASSRLS` владельцем только
   dispatcher. Она не владеет схемой или таблицами и получает минимальные права,
   необходимые функции. `aurum_migrator` может `SET ROLE` в неё для миграции.

На текущем этапе обе capability-роли создаются с нулевым ACL. У них нет логина,
пароля, `CONNECT`, доступа к схеме, таблицам, последовательностям или функциям.
Это позволяет проверить cluster bootstrap до появления денежного dispatcher.

## Требования к dispatcher

- `SECURITY DEFINER`, фиксированный `search_path = pg_catalog, pg_temp`,
  статический SQL и `row_security = on`;
- идентичность узла берётся из неизменяемого `session_user` и защищённого binding,
  а не из `app.*` GUC;
- под блокировками сверяются activation, tenant, branch, node, writer epoch,
  register, user и capability `cash_sale_v1`;
- авторизация, FEFO, наличная оплата, остатки, чек, audit, outbox и immutable
  command ledger завершаются одной транзакцией;
- одинаковые `operation_id` и нормализованный payload возвращают прежний
  результат, а другой payload с тем же идентификатором получает конфликт;
- `PUBLIC`, `aurum_app` и `aurum_support` не могут выполнить dispatcher.

## Граница выпуска

Этот ADR не включает Edge writer. До отдельной миграции dispatcher, per-node
enrollment, положительного offline-auth verifier и аппаратных требований ADR-0005
runtime остаётся `DenyAllOfflineAuthVerifier`. Повторный role bootstrap до этого
этапа удаляет любые неожиданные memberships и ACL capability-ролей.
