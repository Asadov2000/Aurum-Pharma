using System.Text.Json;
using System.Text.RegularExpressions;

namespace AurumPharma.Desktop.Core;

public enum DesktopBridgeCommandType
{
    Ready,
    ReceiptPrint,
    CashDrawerOpen,
    FileExportRequest,
}

public sealed record DesktopBridgeCommand(DesktopBridgeCommandType Type);

public static partial class DesktopBridgeMessageParser
{
    private const int MaxMessageLength = 8_192;
    private const int MaxIdentifierLength = 128;
    private const int MaxFileNameLength = 180;
    private static readonly char[] InvalidFileNameChars = ['<', '>', ':', '"', '/', '\\', '|', '?', '*'];

    public static bool TryParse(string? json, out DesktopBridgeCommand? command)
    {
        command = null;
        if (string.IsNullOrWhiteSpace(json) || json.Length > MaxMessageLength)
        {
            return false;
        }

        try
        {
            using var document = JsonDocument.Parse(
                json,
                new JsonDocumentOptions
                {
                    AllowTrailingCommas = false,
                    CommentHandling = JsonCommentHandling.Disallow,
                    MaxDepth = 8,
                });

            var root = document.RootElement;
            if (root.ValueKind != JsonValueKind.Object ||
                !HasOnlyProperties(root, "type", "payload") ||
                !TryGetText(root, "type", 64, out var type))
            {
                return false;
            }

            return type switch
            {
                "aurum.desktop.ready" => TryParseReady(root, out command),
                "aurum.receipt.print" => TryParseReceiptPrint(root, out command),
                "aurum.cash-drawer.open" => TryParseCashDrawer(root, out command),
                "aurum.file-export.request" => TryParseFileExport(root, out command),
                _ => false,
            };
        }
        catch (JsonException)
        {
            return false;
        }
    }

    private static bool TryParseReady(JsonElement root, out DesktopBridgeCommand? command)
    {
        command = null;
        if (root.TryGetProperty("payload", out var payload) &&
            payload.ValueKind is not JsonValueKind.Null and not JsonValueKind.Undefined)
        {
            return false;
        }

        command = new DesktopBridgeCommand(DesktopBridgeCommandType.Ready);
        return true;
    }

    private static bool TryParseReceiptPrint(JsonElement root, out DesktopBridgeCommand? command)
    {
        command = null;
        if (!TryGetPayload(root, out var payload) ||
            !HasOnlyProperties(payload, "saleId") ||
            !TryGetText(payload, "saleId", MaxIdentifierLength, out _))
        {
            return false;
        }

        command = new DesktopBridgeCommand(DesktopBridgeCommandType.ReceiptPrint);
        return true;
    }

    private static bool TryParseCashDrawer(JsonElement root, out DesktopBridgeCommand? command)
    {
        command = null;
        if (!TryGetPayload(root, out var payload) ||
            !HasOnlyProperties(payload, "reason", "registerId", "saleId") ||
            !TryGetText(payload, "reason", 32, out var reason) ||
            reason is not "manual" and not "sale-completed" ||
            !TryGetOptionalText(payload, "registerId", MaxIdentifierLength) ||
            !TryGetOptionalText(payload, "saleId", MaxIdentifierLength))
        {
            return false;
        }

        command = new DesktopBridgeCommand(DesktopBridgeCommandType.CashDrawerOpen);
        return true;
    }

    private static bool TryParseFileExport(JsonElement root, out DesktopBridgeCommand? command)
    {
        command = null;
        if (!TryGetPayload(root, out var payload) ||
            !HasOnlyProperties(payload, "fileName", "mimeType", "sizeBytes") ||
            !TryGetText(payload, "fileName", MaxFileNameLength, out var fileName) ||
            !IsSafeFileName(fileName) ||
            !TryGetOptionalMimeType(payload) ||
            !TryGetOptionalSize(payload))
        {
            return false;
        }

        command = new DesktopBridgeCommand(DesktopBridgeCommandType.FileExportRequest);
        return true;
    }

    private static bool TryGetPayload(JsonElement root, out JsonElement payload)
    {
        return root.TryGetProperty("payload", out payload) && payload.ValueKind == JsonValueKind.Object;
    }

    private static bool TryGetText(
        JsonElement element,
        string propertyName,
        int maxLength,
        out string value)
    {
        value = string.Empty;
        if (!element.TryGetProperty(propertyName, out var property) ||
            property.ValueKind != JsonValueKind.String)
        {
            return false;
        }

        value = property.GetString()?.Trim() ?? string.Empty;
        return value.Length is > 0 && value.Length <= maxLength;
    }

    private static bool TryGetOptionalText(JsonElement element, string propertyName, int maxLength)
    {
        if (!element.TryGetProperty(propertyName, out var property) ||
            property.ValueKind == JsonValueKind.Null)
        {
            return true;
        }

        if (property.ValueKind != JsonValueKind.String)
        {
            return false;
        }

        var value = property.GetString()?.Trim() ?? string.Empty;
        return value.Length is > 0 && value.Length <= maxLength;
    }

    private static bool TryGetOptionalMimeType(JsonElement payload)
    {
        if (!payload.TryGetProperty("mimeType", out var property) || property.ValueKind == JsonValueKind.Null)
        {
            return true;
        }

        var value = property.ValueKind == JsonValueKind.String ? property.GetString()?.Trim() : null;
        return value is { Length: > 0 and <= 128 } && MimeTypePattern().IsMatch(value);
    }

    private static bool TryGetOptionalSize(JsonElement payload)
    {
        return !payload.TryGetProperty("sizeBytes", out var property) ||
            property.ValueKind == JsonValueKind.Null ||
            (property.ValueKind == JsonValueKind.Number &&
                property.TryGetInt64(out var value) &&
                value >= 0);
    }

    private static bool IsSafeFileName(string fileName)
    {
        if (fileName.IndexOfAny(InvalidFileNameChars) >= 0 ||
            fileName.Any(character => char.IsControl(character)) ||
            fileName.EndsWith('.') ||
            fileName.EndsWith(' '))
        {
            return false;
        }

        var stem = fileName.Split('.')[0].ToUpperInvariant();
        return stem is not "CON" and not "PRN" and not "AUX" and not "NUL" &&
            !Regex.IsMatch(stem, "^(COM|LPT)[1-9]$", RegexOptions.CultureInvariant);
    }

    private static bool HasOnlyProperties(JsonElement element, params string[] allowedNames)
    {
        var allowed = new HashSet<string>(allowedNames, StringComparer.Ordinal);
        return element.EnumerateObject().All(property => allowed.Contains(property.Name));
    }

    [GeneratedRegex("^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant)]
    private static partial Regex MimeTypePattern();
}
