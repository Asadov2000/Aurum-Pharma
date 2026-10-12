# AI context index

Эта карта помогает быстро найти контекст задачи. Она не описывает поведение
продукта и не заменяет код, тесты, ADR или `AGENTS.md`.

## Быстрый старт задачи

1. Прочитать `AGENTS.md`.
   При первом входе в репозиторий открыть [карту проекта](project-structure.md);
   очередность новых блоков находится в [плане развития](implementation-roadmap.md).
2. Выполнить `scripts/verify-change.ps1 -Mode Auto -PlanOnly`.
3. Искать нужные символы и файлы через `rg`, не читать домен целиком.
4. До редактирования записать краткий task brief:
   - цель и наблюдаемое поведение;
   - что нельзя менять;
   - критичные инварианты;
   - критерии приемки;
   - проверки.
5. Сначала использовать `scripts/verify-change.ps1 -Mode Preflight` для стиля
   и типов, затем `-Mode Quick` для короткого цикла с тестами.
6. Перед commit использовать `scripts/verify-change.ps1 -Mode Auto`.
7. `Preflight` пропускает тесты и сборки; он и `Quick` не заменяют `Auto`.
   Локальные проверки не заменяют GitHub `CI gate`.

Результат последнего локального запуска: `.codex/verification/latest.json`
и `latest.md` ([формат и запуск в один клик](testing.md)). Сначала читайте
краткий отчёт, затем только лог упавшего шага. Сверяйте время запуска, revision
и текущий `git status`: старый отчёт не подтверждает новые изменения.

Показать план без запуска тестов:

```powershell
.\scripts\verify-change.ps1 -Mode Auto -PlanOnly
```

Полная локальная матрица перед релизом:

```powershell
.\scripts\verify-change.ps1 -Mode Full
```

## Карта кода

| Задача | Реализация | Тесты |
|---|---|---|
| Backend-домен | `backend/app/domains/<domain>/` | `backend/tests/domains/<domain>/` |
| Почта/пароль, SMS-вход и контакты | `backend/app/domains/auth/`, `frontend/src/features/auth/`; [ADR-0042](adr/0042-account-login-identities.md), [Payom](runbooks/payom-sms.md) | auth pytest, Vitest, `frontend/e2e/login-identities.spec.ts`; реальные SMS проверяются отдельно |
| Очередь физических возвратов | `backend/app/domains/customer_returns/physical_router.py`, `physical_repository.py`, `physical_service.py`, миграция 0187; `frontend/src/features/customerReturns/PhysicalCustomerReturnsPanel.tsx` | `test_physical_queue.py`, `test_physical_quarantine_read_migration.py`, CustomerReturnsPanel Vitest, `physical-cash-refund.spec.ts`; только подтверждённый карантин, `customer_returns.view`, без себестоимости и действий изменения |
| Очередь смешанных возвратов | `backend/app/domains/customer_returns/mixed_router.py`, `mixed_repository.py`, `mixed_service.py`, миграция 0192; `frontend/src/features/customerReturns/MixedCustomerReturnsPanel.tsx` | `test_mixed_queue.py`, `test_mixed_quarantine_read_migration.py`, mixed customerReturns Vitest и `physical-cash-refund.spec.ts`; только подтверждённые cash/zero-маркеры, точные дробные пачки и физические единицы, `customer_returns.view`, без себестоимости и действий изменения |
| Подготовка окончательного решения по смешанному карантину | [ADR-0051](adr/0051-mixed-quarantine-disposition.md), `backend/app/domains/customer_returns/mixed_disposition.py` | `test_mixed_disposition.py`; строгая цель, весь исторический объём позиции, канонический хеш. Только чистый контракт: запись в БД, проверка полномочий и HTTP ещё не подключены |
| Внутренняя запись решения по смешанному карантину | [ADR-0051](adr/0051-mixed-quarantine-disposition.md), `mixed_disposition_repository.py`, `mixed_disposition_service.py`, `mixed_disposition_schemas.py`, миграция 0193 | disposition journal/service и isolation pytest; самостоятельный resolve-доступ, подтверждённый источник, полный объём, неизменяемость, повтор и конкуренция. Решённые цели исключены из ожидающих 0192; история доступна через отдельную view-функцию. HTTP записи/истории и UI решений ещё нужны |
| RLS и инварианты БД | модели, миграции, PostgreSQL triggers | `backend/tests/isolation/` |
| Контракт и алгоритм автономного входа v1 | [offline-auth-lease-v1.md](offline-auth-lease-v1.md), `backend/app/domains/sync/offline_auth_lease.py`, доверенное состояние в `offline_auth_lease_state.py`, модели lease в `schemas.py`; обязательная `tenant_access_revision` и независимый sealed floor; runtime DenyAll | `backend/tests/domains/sync/test_offline_auth_lease_v1.py`, `test_offline_auth_lease_pipeline_v1.py`, V0-регрессии |
| Реальная подпись lease v1 | [offline-lease-signature-v1.md](offline-lease-signature-v1.md), `offline_lease_signature.py`; явно доверенный immutable keyring | `test_offline_lease_signature.py`, интеграционный pipeline с реальным Ed25519; production issuer/time/keyring delivery ещё нужны |
| Алгоритм Cloud-выпуска lease v1 | [offline-lease-issuance-v1.md](offline-lease-issuance-v1.md), `offline_lease_issuer.py`, `offline_lease_issuance_state.py`, `offline_tenant_access.py`; trusted tenant policy сужает команды, явный commit | `test_offline_lease_issuer.py`, `test_offline_tenant_access.py`; production trusted adapter, key provisioning и HTTP ещё нужны |
| Неактивный приём offline policy | [offline-policy-inbox-v1.md](offline-policy-inbox-v1.md), `offline_policy_signature.py`, `offline_policy_service.py`, `offline_policy_repository.py`, служебный `offline_policy_import.py`, миграция 0158 | `test_offline_policy_signature.py`, `test_offline_policy_service.py`, `test_offline_policy_repository.py`, RLS/immutable/roundtrip; аппаратное принятие и автоматическая доставка ещё нужны |
| Локальный credential и TPM protector | [offline-local-credentials-v1.md](offline-local-credentials-v1.md), `backend/app/domains/sync/local_credentials.py`, `desktop/AurumPharma.Desktop.Core/Security/`; не подключены к runtime | `test_local_credentials.py`, desktop protector tests; реальные TPM/service ACL и anti-rollback не приняты |
| Сохраняемые попытки и локальные сессии | [offline-local-sessions-v1.md](offline-local-sessions-v1.md), `local_auth_service.py`, `local_auth_state.py`, `local_auth_repository.py`, миграция 0154; независимая проверка политики до проверок доступа и после пароля, runtime закрыт | `test_local_auth_service.py`, `test_local_auth_repository.py`, `tests/isolation/test_local_auth_migration.py`; аппаратный protected-state adapter ещё нужен |
| Сырые часы TPM на Windows | [windows-tpm-clock-reader.md](windows-tpm-clock-reader.md), `Security/TpmClock.cs`, `WindowsTpmClockReader.cs`, `TpmClockContinuity.cs`, `AurumPharma.Desktop.TpmProbe/` | desktop fake transport и continuity tests; явный read-only probe; не UTC, не аттестация и не разрешающий runtime |
| События питания и сырые TPM-наблюдения | [windows-tpm-power-continuity.md](windows-tpm-power-continuity.md), `Security/WindowsTpmClockObservationSession.cs`, `WindowsPowerContinuityRegistration.cs` | fake lifecycle/cleanup/race tests и явный native probe; Windows-служба и trusted UTC ещё не подключены |
| Явная проверка TPM в Windows preflight | [windows-tpm-readiness.md](windows-tpm-readiness.md), `scripts/windows-host-readiness.ps1 -CheckTpm`, `scripts/lib/windows-tpm-readiness.ps1` | `scripts/test-windows-tpm-readiness.ps1`; без build/restore и без допуска автономного входа |
| Backend core/middleware | `backend/app/core/`, `backend/app/middleware/` | `backend/tests/core/`, `backend/tests/middleware/` |
| Поштучная продажа: настройки и подготовительные компоненты v2 | [unit-sales.md](unit-sales.md), [ADR-0050](adr/0050-explicit-unit-sale-accounting.md), `backend/app/domains/catalog/unit_sales.py`, `backend/app/domains/incoming/unit_preparation.py`, `incoming/service.py`, `backend/app/domains/inventory/unit_stock.py`, `inventory/unit_ledger_service.py`, `pos/unit_money.py`, `pos/unit_line_service.py`, `pos/unit_contracts.py`, `pos/unit_documents.py`, `inventory/unit_report_service.py`, `pos/unit_return_service.py`, `pos/unit_cart_service.py`, `pos/unit_cart_quote.py`, `pos/unit_cash_confirmation.py`, `pos/unit_cash_intent_service.py`, `pos/unit_prescription_service.py`, `pos/unit_cart_stock.py`, `pos/unit_checkout_preparation.py`, `pos/unit_events.py`, `pos/unit_event_storage.py` | catalog/incoming preparation, unit stock/ledger/cost, деньги и документы v2, RLS и миграции 0161–0172; prepared-источники сохраняют запрет финансового завершения. Внутренние отчёты журнала единиц и события v2 ещё требуют подключения к рабочим потребителям |
| Физические продажи v3: штуки, блистеры, пачки и возвраты | [unit-sales.md](unit-sales.md), `backend/app/domains/pos/physical_cart_router.py`, `pos/physical_cash_checkout_router.py`, `pos/physical_cash_checkout_service.py`, `pos/physical_cash_refund_router.py`, `pos/physical_cash_refund_service.py`, `pos/physical_cash_refund_preview_service.py`, `pos/physical_zero_return_router.py`, `pos/physical_zero_return_service.py`, `pos/physical_receipt_read.py`; `frontend/src/features/pos/PhysicalCartEditor.tsx`, `PhysicalCashPanel.tsx`, `frontend/src/features/sales/SaleDetailModal.tsx`, `PhysicalRefundModal.tsx`, `physicalRefund.ts`, `physicalRefundRecovery.ts` | physical checkout/refund API, policy, zero-return и receipt pytest; PhysicalCashPanel, SaleAreaPhysicalCart, SaleDetailModal, physical-refund-recovery и physical-zero-return Vitest; `frontend/e2e/physical-cash-refund.spec.ts`. HTTP и UI связывают физическое списание, наличную оплату, исторический чек и возврат в карантин. Нулевая историческая сумма допускает отдельный приём без выплаты и новой продажи; восстановление результата не повторяет операцию. Смешанная корзина с прежними строками, электронная оплата, дальнейшая обработка карантина, общий reporting и Edge V3 остаются отдельными этапами. Реализация в ветке требует CI, слияния и миграций перед выпуском |
| Смешанный наличный возврат | `backend/app/domains/pos/mixed_cash_refund_router.py`, `mixed_cash_refund_service.py`, `mixed_cash_refund_preview_service.py`, `mixed_cash_refund_status_service.py`, `mixed_cash_refund_read.py`; `frontend/src/features/sales/MixedRefundModal.tsx`, `mixedRefund.ts`, `mixedRefundRecovery.ts`; миграция 0190; [unit-sales.md](unit-sales.md) | refund API/schema/preview/status pytest, SQL isolation/roundtrip tests; журнал 0189 + один платёж + новый чек v4 атомарно. HTTP подключает расчёт, проведение и восстановление результата без себестоимости и увеличения остатков. UI подключает выбор двух видов товара, проверенный расчёт и устойчивое восстановление того же UUID; чтение/печать/PDF используют сохранённый чек. mixed refund Vitest и physical-cash-refund E2E проверяют потерю ответа, закрытую смену и одну выплату. Нулевой приём подключён через mixed_zero_return API/service, миграцию 0191 и общий барьер восстановления; нет платежа, чека выплаты или увеличения доступного остатка. Очередь смешанного карантина подключена отдельным read-only API и интерфейсом; окончательные решения ещё не оформляются |
| Фоновые задачи | `backend/app/tasks/` | тесты соответствующего домена |
| Frontend-фича | `frontend/src/features/<domain>/` | `frontend/tests/features/<domain>/` |
| Личный профиль и свои назначения | [personal-profile.md](personal-profile.md), `backend/app/domains/auth/profile_*`, `frontend/src/features/profile/` | auth profile/RLS pytest, profile Vitest, `frontend/e2e/profile.spec.ts` |
| Личная безопасность: способы входа, защита и устройства | [account-security-page.md](account-security-page.md), `frontend/src/features/auth/SecurityPage.tsx`, `AccountIdentityPanel.tsx`, `MfaSettingsPanel.tsx`; [ADR-0033](adr/0033-optional-account-mfa.md) | auth Vitest, `frontend/e2e/auth.spec.ts`, `optional-mfa.spec.ts`; статусы и формы используют реальный API |
| Свои кассовые смены | [my-work-shifts.md](my-work-shifts.md), POS `/shifts/mine`, `frontend/src/features/my-work/` | POS personal shifts/RLS pytest, my-work Vitest, `frontend/e2e/my-work.spec.ts` |
| Личные уведомления и настройки получения | [notifications-page.md](notifications-page.md), `frontend/src/features/notifications/`, существующий notifications API | notifications Vitest, new-device flow в `frontend/e2e/auth.spec.ts`; обязательный in_app и черновик настроек сохраняются |
| Ручные зарплатные сведения | [manual-staff-pay.md](manual-staff-pay.md), `backend/app/domains/staff_pay/`, `frontend/src/features/staff-pay/`, миграция0160 | staff_pay pytest/RLS, staff-pay Vitest, `frontend/e2e/staff-pay.spec.ts`; без автоматического начисления и движения кассы |
| Карточки команды и просмотр сотрудника | [employees-page.md](employees-page.md), `frontend/src/features/roles/UsersPage.tsx`, `EmployeeDirectory.tsx`, `EmployeeDetails.tsx` | roles Vitest, employee lifecycle/session/assignment E2E; просмотр не выдаёт право изменения |
| Проверка изменений в конструкторе ролей | [roles-builder-page.md](roles-builder-page.md), `frontend/src/features/roles/RoleBuilderModal.tsx`, `RolesPage.tsx` | roles Vitest, owner-role-builder и interface-layout E2E; версия редактора фиксирована, публикация проверяет актуальный доступ |
| Отчёты, выгрузки и финансовая сводка | [Проверка отчётности](reporting-audit.md), `backend/app/domains/pos/`, `backend/app/domains/dashboard/`, `frontend/src/features/reports/` | pytest POS/dashboard/billing, Vitest reports/dashboard, E2E выгрузок и закрытия смены |
| Наличные расчёты с Aurum | [ADR-0048](adr/0048-cash-settlements-with-aurum.md), billing cash commands, `cashTypes.ts`, `CashPaymentsWorkspace.tsx`, `CashPaymentsPanel.tsx`; миграция 0159 | Независимое подтверждение, tenant RLS, повтор/баланс/документ; наличные возвраты и фискализация отдельно |
| Публичный сайт и способы установки | `website/src/`, `website/scripts/`; [ADR-0041](adr/0041-public-website-boundary.md) | `website/scripts/test.mjs`, браузерная проверка сайта, CI gate |
| Границы компонентов и план развития | [project-structure.md](project-structure.md), [implementation-roadmap.md](implementation-roadmap.md) | отличать реализованные пути и проверенные результаты от планов |
| Windows-клиент / будущий установщик | `desktop/AurumPharma.Desktop/`, `desktop/AurumPharma.Desktop.Core/`; [desktop-host-implementation.md](desktop-host-implementation.md), [desktop-bridge.md](desktop-bridge.md) | desktop core tests, packaged WinUI build, `scripts/windows-host-readiness.ps1`; установщик и offline runtime ещё не выпущены |
| Автономность точки до 30 дней | [ADR-0043](adr/0043-thirty-day-branch-edge-continuity.md), [контракт автономной работы](offline-operations-contract.md), `backend/app/domains/sync/` | fault matrix, Edge integration, Windows и recovery-тесты; текущий runtime остаётся fail-closed |
| Зашифрованная репетиция read-only Edge recovery | [edge-encrypted-recovery-drill.md](edge-encrypted-recovery-drill.md), `scripts/smoke-edge-cold-read.ps1 -VerifyRestore`, `scripts/edge-read-recovery-drill.sh` | Restic, точный snapshot ID, неверный ключ и повреждённый ciphertext; синтетика на одном хосте, не production backup или TPM-перенос |
| Общий UI | `frontend/src/components/` | `frontend/tests/components/` |
| Правой кнопкой и личные быстрые действия | [context-actions.md](context-actions.md), `ActionMenu.tsx`, `RecordActionMenu.tsx`, `frontend/src/features/settings/quickActions.ts` | auth/preferences, Vitest меню и настроек, `frontend/e2e/context-actions.spec.ts`; каталог, партии, приёмка, сотрудники, чеки и строки чека кассы |
| Общие frontend-механизмы | `frontend/src/lib/`, `frontend/src/stores/` | `frontend/tests/lib/`, тесты фичи |
| Browser flow | `frontend/e2e/*.spec.ts` | изолированный `scripts/e2e-isolated.ps1` |
| Тестовая инфраструктура и быстрый цикл | `docs/testing.md`, `docs/e2e.md` | fixtures backend, Vitest environments, E2E guards |
| Архитектурное решение | `docs/adr/` | код и тесты имеют приоритет для текущего поведения |
| Продуктовые границы | `docs/spec-v3.md` | сверять с реализацией |
| Схема данных | `docs/db-schema-v2.md` | миграции являются текущей истиной |
| Текущее состояние и риски | `docs/development-state.md`, `docs/known-issues.md` | обновлять после широкого аудита или релизного gate |
| Юридический и пилотный допуск | `docs/compliance/pilot-readiness-manifest.json`, `docs/compliance/pilot-legal-gate.md` | `scripts/Test-PilotReadiness.ps1 -Mode Validate`; `Enforce` обязан падать до подписанного допуска |

Точные версии зависимостей находятся только в `backend/pyproject.toml`,
`backend/poetry.lock`, `frontend/package.json` и `frontend/pnpm-lock.yaml`.

## Короткая передача задачи

Передача между AI-задачами должна занимать не больше 10-12 строк:

```text
Цель:
Ветка / worktree:
Готово:
Измененные файлы:
Принятые решения:
Проверки и результат:
Текущий блокер (если есть):
Следующее конкретное действие:
```

Не вставлять успешные логи, большие diff и содержание уже принятых документов.
Для ошибки сохранять название упавшего шага и читать только его отдельный лог.
