# Windows host implementation blueprint

Документ описывает, как реализовать Windows-приложение Aurum Pharma после того,
как `scripts/windows-host-readiness.ps1 -FailOnMissing` станет зелёным и
`scripts/windows-host-scaffold.ps1 -Create` сможет создать WinUI 3 проект.

## Цель

Windows-приложение не должно становиться отдельной версией продукта. Оно хостит
тот же frontend через WebView2 и добавляет только нативные интеграции:

- режим запуска `windows-desktop`;
- bridge `window.aurumDesktop`;
- обработку сообщений из `frontend/src/lib/desktopBridge.ts`;
- работу с локальными устройствами кассы.

Web-версия остаётся основной. Если desktop-функция недоступна, frontend обязан
работать через браузерный fallback.

## Стандартный проект

Scaffold-команда:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows-host-scaffold.ps1 -Create
```

Ожидаемый результат:

- `desktop/AurumPharma.Desktop`;
- C# WinUI 3;
- `net10.0`;
- packaged app по умолчанию;
- успешный `dotnet build`.

Unpackaged вариант разрешён только если нужен прямой `.exe` launch-flow:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows-host-scaffold.ps1 -Create -Unpackaged
```

## Минимальная структура host-проекта

После scaffold не ломай template-структуру. Добавляй слои постепенно:

```text
desktop/AurumPharma.Desktop/
  App.xaml
  App.xaml.cs
  MainWindow.xaml
  MainWindow.xaml.cs
  Services/
    DesktopBridgeService.cs
    HostSettings.cs
    HostSettingsService.cs
    ReceiptPrintService.cs
    CashDrawerService.cs
    FileExportService.cs
    BarcodeScannerService.cs
  Models/
    DesktopBridgeMessage.cs
    DesktopBridgeCapabilities.cs
  Diagnostics/
    HostLog.cs
```

На первом этапе допустимо оставить сервисы-заглушки, которые логируют запрос и
ничего не делают с устройством. Главное: message routing должен быть строгим и
не выполнять неизвестные команды.

## MainWindow и WebView2

`MainWindow` должен быть простым shell:

- один `WebView2` занимает всё окно;
- заголовок окна: `Aurum Pharma`;
- без собственной навигации WinUI поверх web-интерфейса;
- без отдельного меню, пока нет реального desktop-only workflow.

Источник frontend:

- dev: `http://localhost:5173`;
- production позже: URL из host settings;
- fallback при пустом URL: не открывать произвольный адрес, а показать
  понятную ошибку в окне host.

## Инъекция bridge

Bridge надо добавлять до загрузки страницы через
`CoreWebView2.AddScriptToExecuteOnDocumentCreatedAsync`.

Минимальный injected shape:

```js
window.aurumDesktop = {
  appVersion: "0.1.0",
  platform: "windows",
  capabilities: ["receipt-print", "barcode-scanner", "cash-drawer", "file-export"],
  postMessage(message) {
    window.chrome.webview.postMessage(message);
  }
};
```

Не выполняй JavaScript, пришедший из frontend как строка. Host принимает только
JSON-compatible сообщения с известным `type`.

## Message routing

Единая точка входа: `CoreWebView2.WebMessageReceived`.

Обработка:

| Message type | Service | Первый этап |
|---|---|---|
| `aurum.desktop.ready` | `DesktopBridgeService` | записать диагностику, подтвердить runtime |
| `aurum.receipt.print` | `ReceiptPrintService` | проверить `saleId`, логировать запрос |
| `aurum.cash-drawer.open` | `CashDrawerService` | проверить `reason`, логировать запрос |
| `aurum.file-export.request` | `FileExportService` | сопоставить с WebView2 download event |

Неизвестный `type`:

- не выполнять;
- не показывать кассиру системную ошибку;
- писать warning в host log без PII.

## Сканер штрихкодов

Когда появится нативный scanner integration, host должен отправлять событие в
страницу:

```js
window.dispatchEvent(
  new CustomEvent("aurum-desktop-barcode-scanned", {
    detail: { code: "4600123456789" }
  })
);
```

Правила:

- пустой код не отправлять;
- код trim-ить на стороне host;
- не отправлять строки длиннее 256 символов;
- не логировать полный поток сканирования, только диагностические счётчики.

## File export

Frontend уже вызывает обычный browser download и дополнительно отправляет
`aurum.file-export.request`.

Host-side V1:

1. слушает WebView2 download events;
2. использует последний `aurum.file-export.request` как metadata;
3. показывает системный save dialog или сохраняет в разрешённую папку;
4. не принимает полный путь от frontend.

Это важное правило безопасности: web-слой не должен выбирать путь на диске.

## Receipt print

На первом этапе не пытайся печатать без полноценного источника данных. Message
`aurum.receipt.print` содержит только `saleId`, поэтому host должен либо:

- делегировать печать существующему web-flow;
- либо позже получать receipt payload через backend/API с корректной auth-схемой.

До проектирования auth-схемы для host не надо читать токены из web storage
нативным кодом.

## Cash drawer

Первый этап:

- строгая валидация `reason`;
- optional `registerId` и `saleId`;
- логирование факта запроса без персональных данных.

Реальное открытие ящика добавляется только после выбора hardware protocol:

- через принтерный ESC/POS pulse;
- через COM/USB device;
- через vendor SDK.

## Settings

Минимальные настройки host:

```json
{
  "appUrl": "http://localhost:5173",
  "capabilities": ["receipt-print", "barcode-scanner", "cash-drawer", "file-export"],
  "logLevel": "Information"
}
```

Для packaged app настройки должны храниться в package-safe storage. Для
unpackaged app нельзя полагаться на package identity.

## Проверка готовности первой версии

Перед коммитом host-реализации:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows-host-readiness.ps1 -FailOnMissing
powershell -ExecutionPolicy Bypass -File .\scripts\windows-host-scaffold.ps1 -Create
dotnet build .\desktop\AurumPharma.Desktop\AurumPharma.Desktop.csproj -c Debug
```

После build нужно запустить приложение и подтвердить объективно:

- появилось окно `Aurum Pharma`;
- WebView2 загрузил frontend;
- `RuntimeSurfaceBadge` показывает `Windows`;
- frontend отправил `aurum.desktop.ready`;
- неизвестное bridge-сообщение не приводит к падению host.

Параллельно web safety-net остаётся обязательным:

- backend pytest;
- frontend vitest;
- Playwright e2e.

## Что не делать в первой desktop-итерации

- Не делать отдельный desktop UI вместо web-интерфейса.
- Не добавлять офлайн-кассу.
- Не читать refresh/access token из localStorage нативным кодом.
- Не добавлять vendor SDK без выбранного реального оборудования.
- Не хранить локальные чеки и продажи без отдельного offline-design.
- Не принимать filesystem path от frontend.
