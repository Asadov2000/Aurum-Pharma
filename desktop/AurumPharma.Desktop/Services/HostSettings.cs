namespace AurumPharma_Desktop.Services;

internal sealed record HostSettings(Uri AppUri)
{
    public string TrustedOrigin => AppUri.GetLeftPart(UriPartial.Authority);
}
