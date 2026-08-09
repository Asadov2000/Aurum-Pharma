# ADR-0012: Непубликуемое авторизационное ядро Edge cash sale

- Статус: принят
- Дата: 2026-08-09

## Контекст

Cloud checkout уже атомарно фиксирует продажу, FEFO-списание, оплату, номер чека
и outbox-событие. Протокол single-writer умеет передавать филиал Edge-узлу, но
runtime Edge writer намеренно выключен: отсутствуют production mTLS, TPM,
trusted clock, anti-rollback storage и положительный offline-auth verifier.

Нельзя включать неполный runtime. При этом контракт денежной команды должен быть
готов до появления Windows-службы безопасности и публичного dispatcher.

## Решение

Добавить внутренний `EdgeCashSaleKernel`, не подключенный к FastAPI и runtime
composition. На каждую команду он передает подписанный grant в
`OfflineAuthVerifier`. Положительный verifier обязан в одной serialized
транзакции повторно проверить подпись, trusted clock, device possession, local
session, authorization revision и anti-rollback state.

После проверки kernel сравнивает полный scope команды: activation, tenant,
branch, Edge node, register, writer epoch, user и capability. Он передает
существующему атомарному checkout только одну наличную оплату, не принимает
draft, prescription, payment attempt, card, QR, mixed payment или return.

## Граница выпуска

Этот ADR не разрешает DB-запись Edge writer. Общие функции номера чека и outbox
остаются Cloud-only. Не добавляются миграция, HTTP route, профиль `edge_writer`,
положительный offline-auth verifier или production-флаг. До выполнения ADR-0005
runtime продолжает использовать `DenyAllOfflineAuthVerifier`.

## Последствия

- Авторизационный контракт и cash-only dispatch можно тестировать без открытия
  финансовой поверхности БД.
- Заранее проверенный principal нельзя сохранить и повторно использовать после
  истечения grant: verifier вызывается для каждой команды.
- Следующий обязательный этап: отдельная непривилегированная Edge DB-роль и узкий
  dispatcher. Нельзя строить защиту на изменяемом `app.edge_node_id` GUC.
- После этого отдельно идут полный bootstrap, Edge Security Authority и
  Edge-to-Cloud ingest.
