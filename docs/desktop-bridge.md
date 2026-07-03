# Desktop bridge для Windows-приложения

Этот документ фиксирует контракт между web-frontend Aurum Pharma и будущей
Windows-оболочкой WinUI 3 + WebView2. Цель: один UI работает и в браузере, и в
Windows-приложении, а нативные возможности подключаются через небольшой bridge.

## Принцип

Frontend остаётся главным интерфейсом. Windows-приложение только хостит его в
WebView2 и принимает строго типизированные команды:

- печать чека;
- открытие денежного ящика;
- сохранение файлов через системный диалог;
- передача штрихкодов из нативного сканера в web-кассу.

Если bridge недоступен, web-версия должна продолжать работать обычным
браузерным способом.

## Определение Windows-режима

Frontend считает запуск Windows-приложением, если выполняется хотя бы одно
условие:

- есть `window.aurumDesktop`;
- есть `window.chrome.webview`;
- user-agent содержит `AurumPharmaDesktop`.

Код находится в `frontend/src/lib/runtime.ts` и `frontend/src/lib/desktopBridge.ts`.

## Проверка готовности Windows-host

Перед созданием WinUI 3 проекта запусти read-only аудит:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows-host-readiness.ps1
```

Скрипт ничего не устанавливает и не меняет в системе. Он проверяет:

- версию Windows;
- Developer Mode;
- установленные .NET SDK;
- наличие шаблона `dotnet new winui`;
- Visual Studio workloads для WinUI;
- Windows SDK;
- MSBuild.

Для строгой проверки, где отсутствие компонента должно давать exit code `1`:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows-host-readiness.ps1 -FailOnMissing
```

Если `dotnet new list winui` не видит шаблон, Windows-приложение пока не
генерируем: сначала нужно поставить WinUI prerequisites через Visual Studio /
Windows App SDK setup.

## Установка WinUI prerequisites

Сначала посмотри план без изменений системы:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows-host-setup.ps1
```

В обычном режиме скрипт только запускает readiness-аудит и показывает, какую
команду он применит. Он не ставит Visual Studio и не меняет настройки Windows.

Реальная установка запускается только явным флагом:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows-host-setup.ps1 -Apply
```

Это применит `infra/windows/winui-prerequisites.yaml` через `winget configure`.
Ожидаемый эффект:

- включение Developer Mode;
- установка или обновление Visual Studio Community;
- установка Managed Desktop, Universal и Windows App SDK C# компонентов;
- повторный строгий readiness-аудит после установки.

После зелёного readiness-аудита можно создавать первый WinUI 3 проект.

## Создание первого WinUI host-проекта

Сначала проверь dry-run:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows-host-scaffold.ps1
```

Он ничего не создаёт. После зелёного readiness-аудита реальное создание:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows-host-scaffold.ps1 -Create
```

Стандартный выбор проекта:

- папка: `desktop/AurumPharma.Desktop`;
- имя проекта: `AurumPharma.Desktop`;
- framework: `net10.0`;
- packaging: packaged WinUI 3 app.

Скрипт не перезаписывает существующую папку без `-Force`. После scaffold он
запускает `dotnet build` для созданного `.csproj`. Если нужен прямой `.exe`
launch-flow, можно создать unpackaged вариант:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows-host-scaffold.ps1 -Create -Unpackaged
```

После создания проекта host-side реализация идёт по
`docs/desktop-host-implementation.md`.

## Глобальный объект

Предпочтительный вариант для Windows-host:

```ts
window.aurumDesktop = {
  appVersion: "0.1.0",
  platform: "windows",
  capabilities: ["receipt-print", "barcode-scanner", "cash-drawer", "file-export"],
  postMessage(message) {
    window.chrome.webview.postMessage(message);
  },
};
```

`capabilities` нужны, чтобы frontend не отправлял команду функции, которой нет
в конкретной сборке desktop-приложения.

Допустимые capability:

| Capability | Назначение |
|---|---|
| `receipt-print` | нативная печать чека |
| `barcode-scanner` | ввод штрихкодов из нативного сканера |
| `cash-drawer` | открытие денежного ящика |
| `file-export` | сохранение PDF/XLSX через desktop-host |

Если доступен только сырой `window.chrome.webview.postMessage`, frontend
считает bridge доступным, но не знает точный список capability.

## Сообщения frontend -> Windows-host

Все сообщения отправляются как JSON-совместимые объекты.

### `aurum.desktop.ready`

Frontend отправляет после старта приложения.

```json
{
  "type": "aurum.desktop.ready"
}
```

### `aurum.receipt.print`

Запрос нативной печати чека.

```json
{
  "type": "aurum.receipt.print",
  "payload": {
    "saleId": "uuid-or-sale-id"
  }
}
```

Правила:

- `saleId` trim-ится;
- пустой `saleId` не отправляется;
- команда отправляется только при capability `receipt-print` или через сырой
  WebView2 bridge без metadata.

### `aurum.cash-drawer.open`

Запрос открыть денежный ящик.

```json
{
  "type": "aurum.cash-drawer.open",
  "payload": {
    "reason": "manual",
    "registerId": "uuid-or-register-id",
    "saleId": "uuid-or-sale-id"
  }
}
```

`reason`:

- `manual` — ручное открытие;
- `sale-completed` — открытие после завершения продажи.

`registerId` и `saleId` опциональные. Пустые значения не отправляются.

### `aurum.file-export.request`

Уведомление Windows-host о скачивании файла. Браузерная загрузка всё равно
запускается как fallback.

```json
{
  "type": "aurum.file-export.request",
  "payload": {
    "fileName": "sales-summary-2026-07-03.xlsx",
    "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "sizeBytes": 12456
  }
}
```

Правила:

- имя файла trim-ится;
- символы, запрещённые в Windows (`<>:"/\\|?*` и control chars), заменяются на
  `_`;
- имена `CON`, `PRN`, `AUX`, `NUL`, `COM1..COM9`, `LPT1..LPT9` запрещены;
- максимальная длина имени файла: `180` символов;
- некорректные `mimeType` и `sizeBytes` не отправляются.

## События Windows-host -> frontend

### Штрихкод из сканера

Windows-host должен вызвать:

```ts
window.dispatchEvent(
  new CustomEvent("aurum-desktop-barcode-scanned", {
    detail: { code: "4600123456789" },
  }),
);
```

Правила frontend:

- код trim-ится;
- пустой код игнорируется;
- максимальная длина: `256` символов.

## Безопасность

- Bridge не должен принимать произвольный JavaScript из frontend.
- Windows-host должен обрабатывать только известные `type`.
- Все файловые операции должны проходить через системный диалог или заранее
  разрешённую папку.
- Путь к файлу не должен приходить из frontend; frontend передаёт только имя,
  MIME type и размер.
- Печать и денежный ящик должны проверять доступность устройства на стороне
  Windows-host и возвращать ошибку в собственные логи host-приложения.

## Проверки в коде

Текущие тесты:

- `frontend/tests/lib/desktopBridge.test.ts`;
- `frontend/tests/lib/download.test.ts`;
- `frontend/tests/lib/runtime.test.ts`;
- `frontend/e2e/runtime-surface.spec.ts`;
- `frontend/e2e/reports-export.spec.ts`.
