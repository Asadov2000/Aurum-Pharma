using AurumPharma.Desktop.Core;

namespace AurumPharma_Desktop.Services;

internal sealed class HostSettingsService
{
    private const string DefaultAppUrl = "http://localhost:5173";
    private const string AppUrlVariable = "AURUM_APP_URL";
    private const string EdgeBranchVariable = "AURUM_EDGE_BRANCH_ID";

    public HostSettings Load()
    {
        var rawBranchId = Environment.GetEnvironmentVariable(EdgeBranchVariable)?.Trim();
        Guid? edgeBranchId = null;
        if (!string.IsNullOrEmpty(rawBranchId))
        {
            if (!Guid.TryParse(rawBranchId, out var parsedBranchId) || parsedBranchId == Guid.Empty)
            {
                throw new InvalidOperationException($"{EdgeBranchVariable} is invalid.");
            }

            edgeBranchId = parsedBranchId;
        }

        var appUrl = Environment.GetEnvironmentVariable(AppUrlVariable)?.Trim();
        if (edgeBranchId is not null)
        {
            if (!AppUriPolicy.TryCreateEdgeOrigin(appUrl, out var edgeUri))
            {
                throw new InvalidOperationException($"{AppUrlVariable} must be an explicit loopback URL in Edge mode.");
            }

            return new HostSettings(edgeUri, edgeBranchId);
        }

        if (string.IsNullOrEmpty(appUrl))
        {
            appUrl = DefaultAppUrl;
        }

        if (!AppUriPolicy.TryCreate(appUrl, out var appUri))
        {
            throw new InvalidOperationException($"{AppUrlVariable} is invalid.");
        }

        return new HostSettings(appUri, edgeBranchId);
    }
}
