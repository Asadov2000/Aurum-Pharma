using AurumPharma.Desktop.Core;

namespace AurumPharma_Desktop.Services;

internal sealed class HostSettingsService
{
    private const string DefaultAppUrl = "http://localhost:5173";
    private const string AppUrlVariable = "AURUM_APP_URL";
    private const string EdgeBranchVariable = "AURUM_EDGE_BRANCH_ID";

    public HostSettings Load()
    {
        var appUrl = Environment.GetEnvironmentVariable(AppUrlVariable)?.Trim();
        if (string.IsNullOrEmpty(appUrl))
        {
            appUrl = DefaultAppUrl;
        }

        if (!AppUriPolicy.TryCreate(appUrl, out var appUri))
        {
            throw new InvalidOperationException($"{AppUrlVariable} is invalid.");
        }

        var rawBranchId = Environment.GetEnvironmentVariable(EdgeBranchVariable)?.Trim();
        Guid? edgeBranchId = null;
        if (!string.IsNullOrEmpty(rawBranchId))
        {
            if (!Guid.TryParse(rawBranchId, out var parsedBranchId))
            {
                throw new InvalidOperationException($"{EdgeBranchVariable} is invalid.");
            }

            edgeBranchId = parsedBranchId;
        }

        return new HostSettings(appUri, edgeBranchId);
    }
}
