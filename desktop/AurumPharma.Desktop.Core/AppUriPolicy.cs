using System.Diagnostics.CodeAnalysis;

namespace AurumPharma.Desktop.Core;

public static class AppUriPolicy
{
    public static bool TryCreate(
        string? value,
        [NotNullWhen(true)] out Uri? appUri)
    {
        appUri = null;
        if (string.IsNullOrWhiteSpace(value) ||
            !Uri.TryCreate(value.Trim(), UriKind.Absolute, out var candidate) ||
            !string.IsNullOrEmpty(candidate.UserInfo))
        {
            return false;
        }

        var allowed = candidate.Scheme == Uri.UriSchemeHttps ||
            (candidate.Scheme == Uri.UriSchemeHttp && candidate.IsLoopback);
        if (!allowed)
        {
            return false;
        }

        appUri = candidate;
        return true;
    }

    public static bool IsSameOrigin(Uri expected, Uri actual)
    {
        return string.Equals(expected.Scheme, actual.Scheme, StringComparison.OrdinalIgnoreCase) &&
            string.Equals(expected.IdnHost, actual.IdnHost, StringComparison.OrdinalIgnoreCase) &&
            expected.Port == actual.Port;
    }
}
