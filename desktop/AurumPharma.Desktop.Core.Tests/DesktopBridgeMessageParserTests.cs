using AurumPharma.Desktop.Core;

namespace AurumPharma.Desktop.Core.Tests;

public sealed class DesktopBridgeMessageParserTests
{
    [Theory]
    [InlineData("{\"type\":\"aurum.desktop.ready\"}", DesktopBridgeCommandType.Ready)]
    [InlineData("{\"type\":\"aurum.receipt.print\",\"payload\":{\"saleId\":\"sale-1\"}}", DesktopBridgeCommandType.ReceiptPrint)]
    [InlineData("{\"type\":\"aurum.cash-drawer.open\",\"payload\":{\"reason\":\"sale-completed\",\"saleId\":\"sale-1\"}}", DesktopBridgeCommandType.CashDrawerOpen)]
    [InlineData("{\"type\":\"aurum.file-export.request\",\"payload\":{\"fileName\":\"report.xlsx\",\"sizeBytes\":42}}", DesktopBridgeCommandType.FileExportRequest)]
    public void TryParseAcceptsKnownValidatedMessages(string json, DesktopBridgeCommandType expected)
    {
        Assert.True(DesktopBridgeMessageParser.TryParse(json, out var command));
        Assert.Equal(expected, command?.Type);
    }

    [Theory]
    [InlineData("{\"type\":\"aurum.unknown\"}")]
    [InlineData("{\"type\":\"aurum.desktop.ready\",\"unexpected\":true}")]
    [InlineData("{\"type\":\"aurum.receipt.print\",\"payload\":{\"saleId\":\"\"}}")]
    [InlineData("{\"type\":\"aurum.cash-drawer.open\",\"payload\":{\"reason\":\"unknown\"}}")]
    [InlineData("{\"type\":\"aurum.file-export.request\",\"payload\":{\"fileName\":\"../secret.txt\"}}")]
    [InlineData("not-json")]
    public void TryParseRejectsUnknownOrInvalidMessages(string json)
    {
        Assert.False(DesktopBridgeMessageParser.TryParse(json, out _));
    }
}
