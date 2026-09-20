namespace AurumPharma_Desktop.Services;

internal sealed record HostSettings(Uri AppUri, Guid? EdgeBranchId)
{
    public string TrustedOrigin => AppUri.GetLeftPart(UriPartial.Authority);
}
