# Явная проверка TPM перед будущей установкой

`scripts/windows-host-readiness.ps1 -CheckTpm` добавляет к проверке инструментов
разработки безопасное чтение TPM через уже существующий developer probe.
Без `-CheckTpm` скрипт не запускает probe и не обращается к TPM.

```powershell
./scripts/windows-host-readiness.ps1 -CheckTpm -FailOnMissing
```

Нужна заранее собранная Release win-x64 версия
`desktop/AurumPharma.Desktop.TpmProbe/bin/Release/net10.0/win-x64/AurumPharma.Desktop.TpmProbe.dll`
и установленный совместимый .NET runtime. Preflight запускает только `dotnet exec`
этого файла: не вызывает build/restore, не устанавливает компоненты, не создаёт
ключи и не меняет питание компьютера. Обычные проверки инструментов остаются
в составе отчёта; это developer preflight, не готовый установщик для клиента.

Положительный результат требует exit 0 и точный JSON-контракт версии 1:
`read-only-available`, четыре успешных наблюдения, `runtimeAdmission=false`
и `hardwareAcceptance=false`. Дубликаты полей, лишние поля, диагностический
текст, неверные типы и несовпадение кода выхода отклоняются. Stdout/stderr и
нативные исключения не выводятся. Процесс ограничен 15 секундами; при превышении
лимита завершается только созданный probe. Отсутствие сборки, runtime,
поддерживаемой платформы или подтверждённых наблюдений считается отсутствующим
условием. С `-FailOnMissing` оно даёт ненулевой exit.

Успех означает только сырые наблюдения под текущим аккаунтом. Он не подтверждает
UTC, TPM attestation, права будущей Windows-службы, устойчивость к откату,
сон/перезапуск или работоспособность месяц без интернета. Автономный вход и касса
этой проверкой не включаются. [Границы TPM](windows-tpm-clock-reader.md) и
[наблюдения питания](windows-tpm-power-continuity.md) остаются обязательными.

`scripts/test-windows-tpm-readiness.ps1` проверяет контракт и жизненный цикл
процесса на синтетических ответах, без TPM. CI запускает его на Windows/Linux
с PowerShell 7 и на Windows с PowerShell 5.1. Реальный read-only probe запускается
только явно, не в тестах.
