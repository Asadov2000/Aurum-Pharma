namespace AurumPharma_Desktop.Services;

internal sealed class HostSettingsService
{
    private const string DefaultAppUrl = "http://localhost:5173";

    public HostSettings Load()
    {
        return new HostSettings(new Uri(DefaultAppUrl));
    }
}
