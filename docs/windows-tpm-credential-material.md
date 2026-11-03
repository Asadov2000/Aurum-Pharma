# Защищённые материалы локального credential

Статус: существующий файл читается с проверкой TPM checkpoint, затем pepper
расшифровывается существующим Windows TPM protector. Это источник данных для
будущего login provider, а не готовый автономный вход. Runtime остаётся DenyAll.

## Один защищённый bundle

`protected_credential_bundle.py` и `ProtectedCredentialBundle` задают отдельный
формат версии 1. Он содержит полный [snapshot кассиров](windows-tpm-cashier-state.md)
без изменения его версии, а также строго одну запись материала для каждого
кассира в том же порядке: user ID, `LocalCredentialVerifierV1` и зашифрованный
`ProtectedLocalSecretEnvelope`. Контекст записи должен полностью совпадать с
контекстом кассира; ID/SPKI ключа envelope — с защищённым protection key.

Checkpoint заверяет **весь внешний bundle**, включая verifier, ciphertext,
права, guard и полную сессию. Хеш — SHA-256 от
`aurum:protected-credential-bundle:v1\0` и точных исходных UTF-8 байтов.
Внутренний digest snapshot не является доказательством checkpoint этого bundle.
Прежний snapshot v1 не принимается вместо bundle; fallback отсутствует.

Общий размер ограничен 1 МиБ, JSON — глубиной 32. Неизвестные и повторные поля,
некорректный Unicode, booleans, float/exponent и нарушенные привязки отклоняются.
Verifier имеет прежний bcrypt cost 12. Ciphertext — canonical standard base64
для 256–512 байтов с шагом 8; реальный protector дополнительно требует точный
размер своего RSA-ключа. Формат не содержит открытого pepper или пароля.

Python-модели скрывают материалы в repr и сохраняют `SecretStr`; только явный
encoder экспортирует фактический verifier. Masked `**********` не является
сохраняемым verifier. Bundle содержит чувствительный password verifier и требует
служебных ACL и защищённого канала, хотя pepper внутри зашифрован.

## Реальный Windows reader

`WindowsTpmCredentialMaterialSource.OpenExisting` открывает только уже
подготовленные файлы, TPM counter и machine key. Независимый доверенный enrollment
должен заранее закрепить scope, device, checkpoint context, index и authorization,
подтвердить происхождение snapshot и материалов, обеспечить directory/service ACL
и единственного владельца NV index. Данные SQL, lease, envelope или UI не могут
создать это доверие. Reader ничего не создаёт, не обновляет и не восстанавливает.

При чтении удерживается исключительный дескриптор bundle. Сначала проверяются
формат и ожидаемые привязки, затем точный внешний hash сравнивается с checkpoint.
Выбирается активный кассир текущего policy floor; sealed tenant restrictions
могут только запретить выбор. Это историческая проверка метаданных, а не свежая
проверка сроков допуска, блокировки попыток или права выполнить команду.

Только после этих проверок настоящий `WindowsTpmSecretProtector` получает
ожидаемые ID/SPKI и context digest из защищённого контекста кассира. После
расшифровки проверяется тот же checkpoint ещё раз. TPM key и bundle-файл должны
закрыться до выдачи материала. Ошибка, подмена, откат, несовпадение поколения
или сбой закрытия необратимо закрывают экземпляр reader и очищают полученный pepper.

Результат `CheckpointVerifiedCredentialMaterial` скрывает материал в ToString,
даёт доступ только в callback `Use` и очищает собственный pepper при Dispose.
Владелец обязан вызвать Dispose. Callback не должен сохранять копии или логировать
материалы; автоматическое стирание immutable verifier/JSON-строк и копий вызывающего
кода не обещается. Результат описывает момент проверки, не остаётся актуальным
после изменения checkpoint и не заменяет сериализацию до SQL commit.

## Проверки и оставшиеся границы

Общая искусственная fixture: `docs/fixtures/protected-credential-bundle-v1.json`
и `.sha256`. Её ciphertext — синтетические байты для проверки формата, не TPM
envelope. Отдельные native-тесты шифруют и расшифровывают pepper настоящим
RSA-OAEP codec через внутренний software seam, используют реальные временные
файлы и checkpoint API с тестовым counter. Аппаратный TPM ими не подтверждён.

Проверяются подмена verifier вместе с известным pepper, восстановление старой
копии, чужой scope/device/context, ограничения кассира, изменение checkpoint во
время расшифровки, ошибки ключа/файла и очистка plaintext после сбоя и Dispose.
Python проверяет совместимость записи с существующей проверкой локального секрета.

Доверенный выпуск и отзыв материалов, свежий trusted UTC, Windows-служба/IPC/ACL,
общая SQL-транзакция login/lease provider, запись следующего защищённого состояния,
recovery и аппаратная приёмка ещё обязательны. E6-06 открыт: **2/7 (4)**.
