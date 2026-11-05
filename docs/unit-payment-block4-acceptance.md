# Техническая приёмка блока 4: электронная и комбинированная оплата

Work-Item-ID: `UNIT-PAYMENT-BLOCK4-ACCEPTANCE-01`.
Базовый main: `52b0a7582725f775888c6ff7f87d591bd0f0dba3`.

## Статус и границы

Это перечень критериев и доказательств для текущего PR, а не объявление зелёного CI.
Номер PR, окончательный head и результат слияния фиксируются после их появления.
Приёмка окончательна только после выполнения условий раздела «Закрытие».

Проверяется существующий технический блок 4: physical и mixed продажи,
card/QR/комбинированные платежи, связанные возвраты и безопасное восстановление.
Под mixed понимается один исходный чек с legacy и physical позициями;
дробные legacy количества остаются точными тысячными, physical — целыми единицами.
Комбинированный случай включает cash, card и QR с исходными UUID компонентов.

Банковских вызовов и автоматической внешней выплаты здесь нет.
Оператор явно сохраняет ручную сверку и подтверждения; финансовое проведение —
отдельное действие. Подтверждение оператора не объявляется ответом банка.
Обучающий режим, Edge и пилот остаются тремя отдельными незакрытыми блоками.
Приёмка не закрывает весь модуль и не означает готовность к production или пилоту.
Новые требования, зависимости, таблицы, миграции и deployment в неё не входят.

## Матрица браузерной приёмки

Основной сценарий: `frontend/e2e/physical-cash-checkout.spec.ts`.
Общий цикл возврата: `frontend/e2e/mixedElectronicRefundFlow.ts`
(`verifyElectronicRefundRecovery`, общий для физических и смешанных чеков).
Матрица ниже задаёт обязательные случаи; успех подтверждается отчётом финального CI.

| Исходная корзина | Оплата | Продажа через интерфейс | Связанный возврат через интерфейс |
| --- | --- | --- | --- |
| Physical | card | Точный план и один завершённый чек | Исходный card UUID и одна выплата |
| Physical | QR | Точный план и один завершённый чек | Исходный QR UUID и одна выплата |
| Physical | cash + card + QR | Все три исходных компонента | Точное распределение по исходным UUID |
| Mixed | card | Legacy + physical в одном чеке | Обе разновидности строк и исходный card UUID |
| Mixed | QR | Legacy + physical в одном чеке | Обе разновидности строк и исходный QR UUID |
| Mixed | cash + card + QR | Обе разновидности строк, три компонента | Исторические количества, суммы и UUID |

Для каждого случая сохраняется потерянный ответ после реально выполненной команды.
Повторное открытие/reload восстанавливает тот же operation ID через GET,
а не создаёт новый платёж, выплату или команду движения товара.
Проверяются закрытая смена, повторное чтение и открытие сохранённого чека.
После восстановления число и идентичность финансовых/складских записей
сравниваются с фактами после первого успешного проведения.
Сохранённый pending-журнал не содержит terminal evidence, чека или себестоимости.
Три прежних synthetic physical refund сценария остаются регрессиями;
они не подменяют шесть случаев продажи и возврата через настоящий интерфейс.

## Разделение клиентской и серверной проверки доступа

`frontend/e2e/refundAccessAcceptance.ts` проверяет поведение браузера:
он накладывает presentation overlay только на ответ `/auth/me`
для readonly и отзыва разрешения. Это не меняет настоящие роли, БД или токены.
Финансовые запросы, серверные обработчики и данные продажи/возврата остаются реальными.
Такой сценарий доказывает блокировку действий интерфейсом и безопасное чтение,
но сам по себе не доказывает отказ сервера клиенту с реально отозванной ролью.

Серверный отказ подтверждается отдельно HTTP/DB/RLS тестами ниже.
Vitest с подменённым транспортом проверяет локальные гонки, состояние формы
и отсутствие команды; он также не выдаётся за проверку PostgreSQL или реального банка.

## Проверяемые доказательства backend и frontend

Все пути в таблице относятся к `backend/tests/` или `frontend/tests/`.

| Критерий | Существующее доказательство |
| --- | --- |
| Card/QR/split продажи без повторного списания | `domains/pos/test_unit_electronic_checkout.py::test_exact_confirmed_plan_posts_once_and_recovers_without_more_effects`; `domains/pos/test_mixed_electronic_checkout.py::test_mixed_confirmed_plan_posts_both_stock_kinds_and_recovers_once` |
| Исторические деньги, количества и UUID | `domains/pos/test_unit_payment_refund_allocation.py::test_repeated_partials_and_final_return_exhaust_each_original_component`, `test_duplicate_method_components_remain_distinct_and_close_exactly`, `test_refund_is_independent_of_ambient_precision_and_traps` |
| Одна выплата, неизменный исходный чек и атомарный rollback | `domains/pos/test_mixed_electronic_refund_posting.py::test_replay_returns_exact_receipt_without_duplicate_financial_rows`, `test_caller_rollback_removes_posting_receipt_payments_and_quarantine`, `test_late_persistence_failure_rolls_back_whole_posting`; аналогичные physical posting тесты |
| Конкурирующие операции и защита непосредственно в SQL | `isolation/test_mixed_electronic_refund_posting_migration.py::test_concurrent_same_posting_recovers_one_exact_sale_and_payment_set`, `test_raw_marker_without_financial_rows_cannot_satisfy_deferred_completion`; миграции 0218/0223: unique source, deferred completeness, RLS |
| Tenant/branch/actor и актуальные права | `domains/pos/test_mixed_electronic_refund_http.py::test_mixed_http_current_authority_protects_commands_and_all_recovery`; `domains/pos/test_unit_electronic_refund_posting_api.py::test_posting_http_rechecks_authority_for_write_and_recovery` |
| Дополнительное право и свежая MFA для внешнего подтверждения возврата | `domains/pos/test_unit_electronic_refund_outcome_api.py::test_confirmation_http_requires_recent_step_up_without_overriding_dependency`, `test_confirmation_http_intersects_both_permission_scopes`; mixed outcome/API сохраняют тот же барьер |
| Closed shift, readonly и неизменное восстановление | `domains/pos/test_mixed_electronic_refund_posting.py::test_committed_lost_response_recovers_after_shift_close_and_readonly`; `domains/pos/test_mixed_electronic_checkout.py::test_readonly_tenant_can_recover_completed_mixed_result_without_new_effects` |
| Отмена и ручная сверка не проводят деньги | `domains/pos/test_mixed_electronic_refund_binding.py::test_early_cancel_never_resurrects_but_allows_a_fresh_review`; `domains/pos/test_mixed_electronic_refund_outcome.py::test_partial_then_complete_evidence_preserves_financial_and_stock_facts` |
| Клиентская блокировка записи и сохранение recovery | `features/sales/mixed-electronic-refund-modal.test.tsx`: closed/readonly/permission, branch/session loss, lost save/post, stale shift; `features/sales/electronic-refund-modal.test.tsx`: physical closed/readonly/permission |
| Приватность pending и защита от гонок | `features/sales/mixedElectronicRefundRecovery.test.ts`: canonical request без receipt/evidence, Web Locks, scoped journal, смена контекста; physical recovery и checkout panel тесты сохраняются |

В PR #424 прошли восемь DB-сценариев options, включая неизменность pending ORM-настроек.
Это историческое доказательство; оно не заменяет полную матрицу CI нового head.

## Закрытие

1. Сверить фактический GitHub main, незавершённые PR и единственного владельца работы.
2. Пройти целевые проверки, независимое ревью и всю матрицу на окончательном head.
3. Получить success всех восьми обязательных контекстов: backend, frontend,
   dependency-audit, e2e, production-containers, tooling-linux, tooling-windows, ci-gate.
4. Снова сверить main/head, выполнить merge с ожидаемым head без обхода защиты.
5. Подтвердить merged-состояние PR и совпадение файлов main с проверенным деревом.

Только после этого технический блок 4 принимается и число открытых блоков
меняется с 4/7 на 3/7. Изменения работающего Aurum и общей БД не выполняются.
Версия развёрнутого приложения и ревизия БД проверяются отдельно от слияния кода.
