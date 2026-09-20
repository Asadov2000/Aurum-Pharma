using AurumPharma.Desktop.Core;
using AurumPharma_Desktop.Diagnostics;

namespace AurumPharma_Desktop.Services;

internal sealed class DesktopBridgeService(HostSettings settings)
{
    public void HandleMessage(string source, string json)
    {
        if (!Uri.TryCreate(source, UriKind.Absolute, out var sourceUri) ||
            !AppUriPolicy.IsSameOrigin(settings.AppUri, sourceUri))
        {
            HostLog.Warning("desktop_bridge_untrusted_source_rejected");
            return;
        }

        if (!DesktopBridgeMessageParser.TryParse(json, out var command) || command is null)
        {
            HostLog.Warning("desktop_bridge_invalid_message_rejected");
            return;
        }

        switch (command.Type)
        {
            case DesktopBridgeCommandType.Ready:
                HostLog.Information("desktop_bridge_ready");
                break;
            case DesktopBridgeCommandType.ReceiptPrint:
                HostLog.Warning("desktop_bridge_receipt_print_unavailable");
                break;
            case DesktopBridgeCommandType.CashDrawerOpen:
                HostLog.Warning("desktop_bridge_cash_drawer_unavailable");
                break;
            case DesktopBridgeCommandType.FileExportRequest:
                HostLog.Warning("desktop_bridge_file_export_unavailable");
                break;
            default:
                HostLog.Warning("desktop_bridge_unknown_command_rejected");
                break;
        }
    }
}
