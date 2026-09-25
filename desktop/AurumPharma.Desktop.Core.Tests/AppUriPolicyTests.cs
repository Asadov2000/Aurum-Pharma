using AurumPharma.Desktop.Core;

namespace AurumPharma.Desktop.Core.Tests;

public sealed class AppUriPolicyTests
{
    [Theory]
    [InlineData("http://localhost:5173/")]
    [InlineData("http://127.0.0.1:4174/login")]
    [InlineData("https://app.aurum.tj/")]
    public void TryCreateAcceptsTrustedTransport(string value)
    {
        Assert.True(AppUriPolicy.TryCreate(value, out var uri));
        Assert.NotNull(uri);
    }

    [Theory]
    [InlineData("http://app.aurum.tj/")]
    [InlineData("file:///C:/app/index.html")]
    [InlineData("javascript:alert(1)")]
    [InlineData("https://user:password@app.aurum.tj/")]
    public void TryCreateRejectsUnsafeUris(string value)
    {
        Assert.False(AppUriPolicy.TryCreate(value, out _));
    }

    [Fact]
    public void IsSameOriginComparesSchemeHostAndEffectivePort()
    {
        var expected = new Uri("https://app.aurum.tj/login");

        Assert.True(AppUriPolicy.IsSameOrigin(expected, new Uri("https://app.aurum.tj/catalog")));
        Assert.False(AppUriPolicy.IsSameOrigin(expected, new Uri("https://public.aurum.tj/")));
        Assert.False(AppUriPolicy.IsSameOrigin(expected, new Uri("http://app.aurum.tj/")));
    }

    [Theory]
    [InlineData("http://localhost:5173/")]
    [InlineData("http://127.0.0.1:8080/catalog")]
    [InlineData("http://[::1]:8080/")]
    public void EdgeOriginAcceptsLoopback(string value)
    {
        Assert.True(AppUriPolicy.TryCreateEdgeOrigin(value, out var uri));
        Assert.NotNull(uri);
    }

    [Theory]
    [InlineData("https://app.aurum.tj/")]
    [InlineData("https://192.168.1.10/")]
    [InlineData("http://localhost.evil.test/")]
    [InlineData("")]
    public void EdgeOriginRejectsRemoteHosts(string value)
    {
        Assert.False(AppUriPolicy.TryCreateEdgeOrigin(value, out var uri));
        Assert.Null(uri);
    }
}
