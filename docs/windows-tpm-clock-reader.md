# Чтение часов TPM 2.0 на Windows

Реализованы `desktop/AurumPharma.Desktop.Core/Security/TpmClock.cs` и
`WindowsTpmClockReader.cs`. Это нативный считыватель **сырых показаний**,
а не защищённые календарные часы для автономного входа. HTTP, Windows bridge
и backend `TrustedClockReading` не подключены; runtime остаётся DenyAll.

## Реализация

Считыватель через Windows TBS отправляет только фиксированную десятибайтовую
команду `TPM2_ReadClock` (`0x00000181`). Контекст запрашивает только TPM 2.0,
locality 0, normal priority; `tbs.dll` загружается исключительно из System32.
Создание ключей, Clear, ClockSet, NV-запись и программный fallback отсутствуют.

TBS-код и TPM-код проверяются отдельно: успешная отправка не означает успешное
выполнение TPM. Parser принимает только точный ответ 35 байтов с правильными
tag, длиной и кодом; числа читаются big-endian без потери unsigned-разрядности.
Флаг `safe` допускает только 0/1. `safe=false` возвращается как явное свойство
сырых данных и не даёт права входа.

Для ненулевого контекста закрытие вызывается при любом результате. Ошибка
закрытия запрещает выдачу показаний. Ошибки скрывают native-коды и внутренние
исключения; на других ОС используется `PlatformNotSupportedException`.
Синхронное чтение пока не вызывается из UI.

## Явная проверка оборудования

После восстановления существующих locked-зависимостей разработчик запускает:

```powershell
dotnet restore desktop/AurumPharma.Desktop.TpmProbe/AurumPharma.Desktop.TpmProbe.csproj --runtime win-x64 --locked-mode
dotnet run --project desktop/AurumPharma.Desktop.TpmProbe/AurumPharma.Desktop.TpmProbe.csproj --configuration Release --runtime win-x64 --no-restore
```

Утилита делает два чтения и печатает JSON только с доступностью, флагами
безопасности, наблюдением непрерывности и движения счётчика. Она не выводит
идентификаторы TPM, ключи, значения счётчиков или данные аккаунта. Exit 0 означает
доступность чтения, exit 2 — недоступность; unsafe/discontinuity отражаются
отдельными boolean и никогда не означают разрешение автономной работы.
CI только собирает probe и выполняет тесты с fake transport, без обращения к TPM.

`TpmClockContinuity.Evaluate` считает наблюдение непротиворечивым только при
`safe=true` у обоих чтений, одинаковых reset/restart и неубывающих Time/Clock.
Равные показания допустимы; движение счётчика проверяется отдельно. Probe
использует эту проверку для `continuityObserved`, не меняя смысл exit 0.
Отсутствие чтения, unsafe, смена эпохи и откат требуют нового доверенного anchor.

Внутренний `TpmClockContinuityMonitor` сериализует наблюдения и сравнивает каждое
с последним успешным, а не только первым. После противоречия или явной
инвалидации его отказ необратим: последующие хорошие показания не восстанавливают
доверие. Это только состояние в памяти. Будущий владелец обязан инвалидировать
его при ошибке чтения, разрыве жизненного цикла и suspend/resume. Создание нового
экземпляра не является доверенным восстановлением. Монитор не подключён к службе,
backend или входу; durable high-water и источник UTC ещё не реализованы.
Нативные события сна/пробуждения связаны с отдельной диагностической сессией:
[реализация и ограничения](windows-tpm-power-continuity.md). Это не lifecycle
Windows-службы и не восстановление допуска после выключения.

На локальном ПК 2026-09-26 probe успешно прочитал часы дважды: оба `safe=true`,
reset/restart между чтениями не изменились, счётчик продвинулся.
`runtimeAdmission=false` и `hardwareAcceptance=false` остаются явными результатами.
Это проверка доступности под текущим аккаунтом, не приёмка Windows-службы.

## Что остаётся обязательным

TPM Clock считает время при питании, а ReadClock не является подписанной
аттестацией. `safe=true` не доказывает UTC, подлинность устройства, отсутствие
Clear или сохранность при восстановлении VM/БД. Календарный срок нельзя
вычислять простым прибавлением Clock к UTC-anchor: длительное выключение ПК
иначе может растянуть срок lease. Нужны отдельная принятая политика
непрерывности/выключения, доверенное календарное время, привязка к устройству,
защищённый high-water и recovery. Самостоятельная корректировка часов Windows
или этот probe не заменяют эти условия.

Источники: [Microsoft — TBS context](https://learn.microsoft.com/en-us/windows/win32/api/tbs/nf-tbs-tbsi_context_create),
[отправка команды](https://learn.microsoft.com/en-us/windows/win32/api/tbs/nf-tbs-tbsip_submit_command),
[TCG — структуры и Clock](https://trustedcomputinggroup.org/wp-content/uploads/Trusted-Platform-Module-Library-Family-2.0-Level-00-Revision-1.81-Part-2-Structures_8December2023.pdf),
[TCG — код ReadClock](https://trustedcomputinggroup.org/wp-content/uploads/Trusted-Platform-Module-2.0-Library-Part-2-Version-184_pub.pdf).
