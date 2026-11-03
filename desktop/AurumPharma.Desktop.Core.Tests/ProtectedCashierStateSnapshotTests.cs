using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using AurumPharma.Desktop.Core.Security;

namespace AurumPharma.Desktop.Core.Tests;

public sealed class ProtectedCashierStateSnapshotTests
{
    [Fact]
    public void SharedPythonGoldenFixtureHasExactPayloadAndCredentialContextDigests()
    {
        var raw = ProtectedCashierSnapshotFixture.ReadPayload();
        var snapshot = ProtectedCashierStateSnapshot.Parse(raw);
        var expected = File.ReadAllText(ProtectedCashierSnapshotFixture.RepoFile(
            "docs/fixtures/protected-cashier-state-v1.sha256")).Trim();
        Assert.Equal(expected, Convert.ToHexStringLower(snapshot.PayloadDigest));
        Assert.Equal("69dc977e7bfb23902cec5b080ffb283b92afa632126b46efc66fe5ec0a77a0a1",
            Convert.ToHexStringLower(snapshot.Cashiers[0].CredentialContext.ContextDigestSha256));
        Assert.Equal(snapshot.Cashiers[0].Guard.ContextDigest,
            Convert.ToHexStringLower(snapshot.Cashiers[0].CredentialContext.ContextDigestSha256));
        Assert.Equal(6, snapshot.Scope.WriterEpoch);
        Assert.Equal(14, snapshot.PolicyFloor.Policy.Version);
        Assert.Equal(102, snapshot.ObservedTime.MonotonicCounter);
        Assert.Equal("sale.cash.complete", snapshot.WorkplaceSession!.Confirmation!.Command);
        snapshot.AssertBinding(ExpectedScope(), ExpectedDevice(), Enumerable.Repeat((byte)0xEF, 32).ToArray());
    }

    [Fact]
    public void ExactRawBytesAreHashedWithoutCanonicalizingOrReorderingJson()
    {
        var first = ProtectedCashierStateSnapshot.Parse(ProtectedCashierSnapshotFixture.ReadPayload());
        var json = ProtectedCashierSnapshotFixture.Json();
        var pretty = JsonSerializer.SerializeToUtf8Bytes(json, new JsonSerializerOptions { WriteIndented = true });
        var second = ProtectedCashierStateSnapshot.Parse(pretty);
        Assert.Equal(first.Scope, second.Scope);
        Assert.Equal(first.DeviceBinding, second.DeviceBinding);
        Assert.Equal(first.Cashiers[0].Guard.ContextDigest, second.Cashiers[0].Guard.ContextDigest);
        Assert.NotEqual(first.PayloadDigest, second.PayloadDigest);
    }

    [Fact]
    public void PublishedSnapshotCannotBeMutatedThroughInputArraysDigestsCollectionsOrFormatting()
    {
        var raw = ProtectedCashierSnapshotFixture.ReadPayload();
        var snapshot = ProtectedCashierStateSnapshot.Parse(raw);
        var digest = snapshot.PayloadDigest;
        var context = snapshot.CheckpointContextSha256;
        Array.Fill(raw, (byte)0);
        snapshot.PayloadDigest[0] ^= 1;
        snapshot.CheckpointContextSha256[0] ^= 1;
        snapshot.Cashiers[0].CredentialContext.ContextDigestSha256[0] ^= 1;
        Assert.Equal(digest, snapshot.PayloadDigest);
        Assert.Equal(context, snapshot.CheckpointContextSha256);
        Assert.Throws<NotSupportedException>(() => ((IList<ProtectedCashierRecord>)snapshot.Cashiers).Clear());
        Assert.Throws<NotSupportedException>(() => ((IList<string>)snapshot.Cashiers[0].Authorization.AllowedCommands).Clear());
        Assert.Throws<NotSupportedException>(() => ((IList<string>)snapshot.PolicyFloor.Policy.BlockedModules).Add("pos"));
        object[] values = [snapshot, snapshot.Scope, snapshot.DeviceBinding, snapshot.PolicyFloor,
            snapshot.PolicyFloor.Policy, snapshot.ObservedTime, snapshot.Cashiers[0],
            snapshot.Cashiers[0].Authorization, snapshot.Cashiers[0].Authorization.Scope,
            snapshot.Cashiers[0].Authorization.Admission, snapshot.Cashiers[0].Authorization.ReauthenticationPolicy,
            snapshot.Cashiers[0].CredentialContext, snapshot.Cashiers[0].Guard,
            snapshot.WorkplaceSession!, snapshot.WorkplaceSession!.Confirmation!];
        foreach (var value in values)
        {
            Assert.Equal(value.GetType().Name, value.ToString());
            Assert.All(value.GetType().GetProperties(), property => Assert.Null(property.SetMethod));
        }
    }

    [Theory]
    [InlineData(0)]
    [InlineData(1)]
    [InlineData(2)]
    [InlineData(3)]
    [InlineData(4)]
    [InlineData(5)]
    public void IndependentExpectedScopeCannotBeRebound(int changed)
    {
        var values = new[] { Id(1), Id(2), Id(3), Id(4), Id(5) };
        if (changed < values.Length) { values[changed] = Id(30); }
        var scope = new ProtectedCashierWorkplaceScope(values[0], values[1], values[2], values[3], values[4], changed == 5 ? 7 : 6);
        var snapshot = ProtectedCashierStateSnapshot.Parse(ProtectedCashierSnapshotFixture.ReadPayload());
        Unavailable(() => snapshot.AssertBinding(scope, ExpectedDevice(), Enumerable.Repeat((byte)0xEF, 32).ToArray()));
    }

    [Fact]
    public void IndependentDeviceAndCheckpointContextCannotBeReboundOrOmitted()
    {
        var snapshot = ProtectedCashierStateSnapshot.Parse(ProtectedCashierSnapshotFixture.ReadPayload());
        var context = Enumerable.Repeat((byte)0xEF, 32).ToArray();
        Unavailable(() => snapshot.AssertBinding(ExpectedScope(), new ProtectedCashierDeviceBinding(Id(30), new string('a', 64)), context));
        Unavailable(() => snapshot.AssertBinding(ExpectedScope(), new ProtectedCashierDeviceBinding(Id(8), new string('f', 64)), context));
        Unavailable(() => snapshot.AssertBinding(ExpectedScope(), ExpectedDevice(), new byte[32]));
        Unavailable(() => snapshot.AssertBinding(ExpectedScope(), ExpectedDevice(), new byte[31]));
        Unavailable(() => snapshot.AssertBinding(null!, ExpectedDevice(), context));
        Unavailable(() => snapshot.AssertBinding(ExpectedScope(), null!, context));
    }

    [Fact]
    public void DenialFloorRevokedCashierExpiredSessionAndEmptyWorkplaceRemainValidData()
    {
        var json = ProtectedCashierSnapshotFixture.Json();
        Set(json, "policy_floor/policy/version", 15);
        Set(json, "policy_floor/policy/tenant_status", "suspended");
        Set(json, "policy_floor/policy/mode", "readonly");
        Set(json, "policy_floor/policy/blocked_modules", JsonNode.Parse("[\"pos\"]"));
        Set(json, "cashiers/0/authorization/status", "revoked");
        Set(json, "workplace_session/expires_at", "2026-11-03T09:00:01Z");
        var snapshot = Parse(json);
        Assert.Equal("revoked", snapshot.Cashiers[0].Authorization.Status);
        Assert.True(snapshot.WorkplaceSession!.ExpiresAt < snapshot.ObservedTime.UtcNow);
        Set(json, "workplace_session", null);
        Set(json, "cashiers", new JsonArray());
        Assert.Empty(Parse(json).Cashiers);
    }

    [Fact]
    public void UuidCaseIsNormalizedOnlyInsideTypedValuesAndExistingCredentialContextHash()
    {
        var raw = Encoding.UTF8.GetString(ProtectedCashierSnapshotFixture.ReadPayload());
        // Change every credential UUID with hex letters, preserving cross-references.
        var changed = raw.Replace("0000000a-0000-4000-8000-000000000001", "0000000A-0000-4000-8000-000000000001", StringComparison.Ordinal)
            .Replace("0000000b-0000-4000-8000-000000000001", "0000000B-0000-4000-8000-000000000001", StringComparison.Ordinal)
            .Replace("0000000c-0000-4000-8000-000000000001", "0000000C-0000-4000-8000-000000000001", StringComparison.Ordinal);
        var snapshot = ProtectedCashierStateSnapshot.Parse(Encoding.UTF8.GetBytes(changed));
        Assert.Equal("69dc977e7bfb23902cec5b080ffb283b92afa632126b46efc66fe5ec0a77a0a1",
            Convert.ToHexStringLower(snapshot.Cashiers[0].CredentialContext.ContextDigestSha256));
        Assert.NotEqual(ProtectedCashierStateSnapshot.Parse(Encoding.UTF8.GetBytes(raw)).PayloadDigest, snapshot.PayloadDigest);
    }

    [Fact]
    public void SequenceLimitsUtcMicrosecondsAndUnicodeCodepointContinuityAreSharedWithPython()
    {
        var json = ProtectedCashierSnapshotFixture.Json();
        Set(json, "observed_time/utc_now", "2026-11-03T09:00:02.123456+00:00");
        Set(json, "observed_time/monotonic_counter", long.MaxValue);
        Set(json, "policy_floor/delivery_sequence", long.MaxValue);
        Set(json, "cashiers/0/guard/failed_attempts", int.MaxValue);
        var continuity = string.Concat(Enumerable.Repeat("\U0001F600", 128));
        Set(json, "observed_time/continuity_id", continuity);
        Set(json, "cashiers/0/guard/continuity_id", continuity);
        Set(json, "workplace_session/continuity_id", continuity);
        var snapshot = Parse(json);
        Assert.Equal(long.MaxValue, snapshot.ObservedTime.MonotonicCounter);
        Assert.Equal(int.MaxValue, snapshot.Cashiers[0].Guard.FailedAttempts);
        Assert.Equal(continuity, snapshot.ObservedTime.ContinuityId);
        Set(json, "observed_time/continuity_id", continuity + "x");
        Unavailable(() => Parse(json));
    }

    [Theory]
    [InlineData("schema_version", "true")]
    [InlineData("schema_version", "1.0")]
    [InlineData("schema_version", "2")]
    [InlineData("scope/writer_epoch", "0")]
    [InlineData("scope/activation_id", "\"00000001-0000-5000-8000-000000000001\"")]
    [InlineData("scope/activation_id", "\"00000001-0000-4000-c000-000000000001\"")]
    [InlineData("scope/activation_id", "\"{00000001-0000-4000-8000-000000000001}\"")]
    [InlineData("checkpoint_context_sha256", "\"EFefefefefefefefefefefefefefefefefefefefefefefefefefefefefefefef\"")]
    [InlineData("device_binding/method", "\"software\"")]
    [InlineData("device_binding/spki_sha256", "\"wrong\"")]
    [InlineData("policy_floor/delivery_sequence", "0")]
    [InlineData("policy_floor/claims_hash", "\"wrong\"")]
    [InlineData("policy_floor/policy/tenant_id", "\"00000002-0000-4000-8000-000000000099\"")]
    [InlineData("policy_floor/policy/version", "0")]
    [InlineData("policy_floor/policy/mode", "\"disabled\"")]
    [InlineData("policy_floor/policy/tenant_status", "\"deleted\"")]
    [InlineData("policy_floor/policy/blocked_modules", "[\"pos\",\"pos\"]")]
    [InlineData("policy_floor/policy/blocked_modules", "[\"pos\",\"catalog\"]")]
    [InlineData("policy_floor/policy/blocked_modules", "[\"unknown\"]")]
    [InlineData("observed_time/utc_now", "\"2026-11-03T12:00:02+03:00\"")]
    [InlineData("observed_time/utc_now", "\"2026-11-03T09:00:02.1234567Z\"")]
    [InlineData("observed_time/utc_now", "\"2026-11-03\"")]
    [InlineData("observed_time/utc_now", "\"2026-02-31T09:00:02Z\"")]
    [InlineData("observed_time/utc_now", "\"2026-11-03T09:00:60Z\"")]
    [InlineData("observed_time/utc_now", "\"2026-11-03T24:00:00Z\"")]
    [InlineData("observed_time/utc_now", "1793696402")]
    [InlineData("observed_time/monotonic_counter", "-1")]
    [InlineData("observed_time/monotonic_counter", "1.25")]
    [InlineData("observed_time/monotonic_counter", "true")]
    [InlineData("observed_time/monotonic_counter", "9223372036854775808")]
    [InlineData("observed_time/continuity_id", "\"\"")]
    [InlineData("cashiers/0/authorization/scope/register_id", "\"00000005-0000-4000-8000-000000000099\"")]
    [InlineData("cashiers/0/authorization/device_binding/key_id", "\"00000008-0000-4000-8000-000000000099\"")]
    [InlineData("cashiers/0/authorization/policy_revision", "0")]
    [InlineData("cashiers/0/authorization/tenant_access_revision", "15")]
    [InlineData("cashiers/0/authorization/subject_revision", "0")]
    [InlineData("cashiers/0/authorization/status", "\"unknown\"")]
    [InlineData("cashiers/0/authorization/credential_audience", "\"aurum-cloud\"")]
    [InlineData("cashiers/0/authorization/allowed_commands", "[]")]
    [InlineData("cashiers/0/authorization/allowed_commands", "[\"sale.cash.complete\",\"sale.cash.complete\"]")]
    [InlineData("cashiers/0/authorization/allowed_commands", "[\"shift.open\",\"sale.cash.complete\"]")]
    [InlineData("cashiers/0/authorization/allowed_commands", "[\"sale.card.complete\"]")]
    [InlineData("cashiers/0/authorization/reauthentication_policy/max_session_seconds", "86401")]
    [InlineData("cashiers/0/authorization/reauthentication_policy/max_confirmation_seconds", "301")]
    [InlineData("cashiers/0/authorization/reauthentication_policy/confirmation_commands", "[\"unknown\"]")]
    [InlineData("cashiers/0/credential_context/audience", "\"aurum-cloud\"")]
    [InlineData("cashiers/0/credential_context/schema_version", "0")]
    [InlineData("cashiers/0/credential_context/protection_key/key_id", "\"00000008-0000-4000-8000-000000000001\"")]
    [InlineData("cashiers/0/credential_context/protection_key/spki_sha256", "\"abababababababababababababababababababababababababababababababab\"")]
    [InlineData("cashiers/0/credential_context/local_credential/revision", "4")]
    [InlineData("cashiers/0/credential_context/scope/user_id", "\"00000007-0000-4000-8000-000000000099\"")]
    [InlineData("cashiers/0/guard/tenant_id", "\"00000002-0000-4000-8000-000000000099\"")]
    [InlineData("cashiers/0/guard/user_id", "\"00000007-0000-4000-8000-000000000099\"")]
    [InlineData("cashiers/0/guard/credential_id", "\"00000009-0000-4000-8000-000000000099\"")]
    [InlineData("cashiers/0/guard/credential_revision", "4")]
    [InlineData("cashiers/0/guard/context_digest", "\"0000000000000000000000000000000000000000000000000000000000000000\"")]
    [InlineData("cashiers/0/guard/failed_attempts", "2147483648")]
    [InlineData("cashiers/0/guard/failed_attempts", "-1")]
    [InlineData("cashiers/0/guard/attempt_sequence", "-1")]
    [InlineData("cashiers/0/guard/last_counter", "103")]
    [InlineData("cashiers/0/guard/last_utc", "\"2026-11-03T09:00:03Z\"")]
    [InlineData("cashiers/0/guard/continuity_id", "\"other-boot\"")]
    [InlineData("workplace_session/session_id", "\"00000013-0000-4000-8000-00000000000\"")]
    [InlineData("workplace_session/lease_id", "\"0000000c-0000-4000-8000-000000000099\"")]
    [InlineData("workplace_session/scope/user_id", "\"00000007-0000-4000-8000-000000000099\"")]
    [InlineData("workplace_session/local_credential/revision", "4")]
    [InlineData("workplace_session/reauthentication_policy/revision", "5")]
    [InlineData("workplace_session/shift_context_id", "\"00000011-0000-4000-8000-000000000099\"")]
    [InlineData("workplace_session/authenticated_at", "\"2026-11-03T09:00:03Z\"")]
    [InlineData("workplace_session/expires_at", "\"2026-11-03T09:00:00Z\"")]
    [InlineData("workplace_session/continuity_id", "\"other-boot\"")]
    [InlineData("workplace_session/monotonic_counter", "103")]
    [InlineData("workplace_session/confirmation/confirmed_at", "\"2026-11-03T08:59:59Z\"")]
    [InlineData("workplace_session/confirmation/confirmed_at", "\"2026-11-03T09:00:03Z\"")]
    [InlineData("workplace_session/confirmation/monotonic_counter", "99")]
    [InlineData("workplace_session/confirmation/monotonic_counter", "103")]
    public void MalformedOrCrossBoundStateIsRejectedWithoutLeakingDetails(string path, string replacement)
    {
        var json = ProtectedCashierSnapshotFixture.Json();
        Set(json, path, JsonNode.Parse(replacement));
        Unavailable(() => Parse(json));
    }

    [Theory]
    [InlineData("")]
    [InlineData("scope")]
    [InlineData("device_binding")]
    [InlineData("policy_floor")]
    [InlineData("policy_floor/policy")]
    [InlineData("observed_time")]
    [InlineData("cashiers/0")]
    [InlineData("cashiers/0/authorization")]
    [InlineData("cashiers/0/authorization/reauthentication_policy")]
    [InlineData("cashiers/0/credential_context")]
    [InlineData("cashiers/0/guard")]
    [InlineData("workplace_session")]
    [InlineData("workplace_session/confirmation")]
    public void EveryObjectRejectsUnknownFields(string path)
    {
        var json = ProtectedCashierSnapshotFixture.Json();
        var target = path.Length == 0 ? json : At(json, path);
        target["unexpected"] = 1;
        Unavailable(() => Parse(json));
    }

    [Theory]
    [InlineData("workplace_session")]
    [InlineData("cashiers/0/guard/blocked_until")]
    [InlineData("policy_floor/policy/expires_at")]
    [InlineData("workplace_session/confirmation")]
    public void NullableMembersMustStillBePresentExplicitly(string path)
    {
        var json = ProtectedCashierSnapshotFixture.Json();
        var segments = path.Split('/');
        var parent = segments.Length == 1 ? json : At(json, string.Join('/', segments[..^1]));
        parent.AsObject().Remove(segments[^1]);
        Unavailable(() => Parse(json));
    }

    [Fact]
    public void RawJsonRejectsDuplicateEscapedDuplicateNonIntegerAndInvalidUnicodeForms()
    {
        var raw = Encoding.UTF8.GetString(ProtectedCashierSnapshotFixture.ReadPayload());
        foreach (var replacement in new[]
        {
            "\"schema_version\":1,\"schema_version\":1",
            "\"schema_version\":1,\"schema_versio\\u006e\":1",
            "\"schema_version\":1e0", "\"schema_version\":NaN", "\"schema_version\":Infinity",
        })
        {
            var mutated = raw.Replace("\"schema_version\":1", replacement, StringComparison.Ordinal);
            Unavailable(() => ProtectedCashierStateSnapshot.Parse(Encoding.UTF8.GetBytes(mutated)));
        }
        var surrogate = raw.Replace("boot-fixture-1", "\\uD800", StringComparison.Ordinal);
        Unavailable(() => ProtectedCashierStateSnapshot.Parse(Encoding.UTF8.GetBytes(surrogate)));
        var invalidUtf8 = ProtectedCashierSnapshotFixture.ReadPayload();
        invalidUtf8[20] = 0xFF;
        Unavailable(() => ProtectedCashierStateSnapshot.Parse(invalidUtf8));
    }

    [Fact]
    public void DuplicateCashiersOversizedCashierArraysPayloadSizeAndDepthAreBounded()
    {
        var json = ProtectedCashierSnapshotFixture.Json();
        var cashiers = json["cashiers"]!.AsArray();
        cashiers.Add(cashiers[0]!.DeepClone());
        Unavailable(() => Parse(json));
        while (cashiers.Count < 257) { cashiers.Add(cashiers[0]!.DeepClone()); }
        Unavailable(() => Parse(json));
        Unavailable(() => ProtectedCashierStateSnapshot.Parse([]));
        Unavailable(() => ProtectedCashierStateSnapshot.Parse(new byte[ProtectedCashierStateSnapshot.MaximumPayloadBytes + 1]));
        var raw = ProtectedCashierSnapshotFixture.ReadPayload();
        var exactlyMaximum = Enumerable.Repeat((byte)' ', ProtectedCashierStateSnapshot.MaximumPayloadBytes).ToArray();
        raw.CopyTo(exactlyMaximum, 0);
        Assert.Single(ProtectedCashierStateSnapshot.Parse(exactlyMaximum).Cashiers);
        var deep = Encoding.UTF8.GetBytes(new string('[', 33) + "0" + new string(']', 33));
        Unavailable(() => ProtectedCashierStateSnapshot.Parse(deep));
    }

    [Fact]
    public void NegativeZeroIntegerIsAcceptedButFloatAndBoolAreNeverInterchangeableWithInteger()
    {
        var json = ProtectedCashierSnapshotFixture.Json();
        Set(json, "cashiers/0/guard/failed_attempts", 0);
        var raw = json.ToJsonString().Replace("\"failed_attempts\":0", "\"failed_attempts\":-0", StringComparison.Ordinal);
        Assert.Equal(0, ProtectedCashierStateSnapshot.Parse(Encoding.UTF8.GetBytes(raw)).Cashiers[0].Guard.FailedAttempts);
    }

    private static ProtectedCashierStateSnapshot Parse(JsonNode value)
        => ProtectedCashierStateSnapshot.Parse(JsonSerializer.SerializeToUtf8Bytes(value));

    private static Guid Id(int value) => Guid.Parse($"{value:x8}-0000-4000-8000-000000000001");

    private static ProtectedCashierWorkplaceScope ExpectedScope()
        => new(Id(1), Id(2), Id(3), Id(4), Id(5), 6);

    private static ProtectedCashierDeviceBinding ExpectedDevice() => new(Id(8), string.Concat(Enumerable.Repeat("ab", 32)));

    private static JsonNode At(JsonNode value, string path)
    {
        foreach (var part in path.Split('/'))
        {
            value = value is JsonArray array ? array[int.Parse(part)]! : value[part]!;
        }
        return value;
    }

    private static void Set(JsonNode root, string path, JsonNode? value)
    {
        var segments = path.Split('/');
        var parent = segments.Length == 1 ? root : At(root, string.Join('/', segments[..^1]));
        if (parent is JsonArray array) { array[int.Parse(segments[^1])] = value; }
        else { parent[segments[^1]] = value; }
    }

    private static void Unavailable(Action action)
    {
        var error = Assert.Throws<CryptographicException>(action);
        Assert.Equal("Protected cashier state is unavailable.", error.Message);
        Assert.Null(error.InnerException);
    }
}

internal static class ProtectedCashierSnapshotFixture
{
    internal static byte[] ReadPayload() => File.ReadAllBytes(RepoFile("docs/fixtures/protected-cashier-state-v1.json"));
    internal static JsonObject Json() => JsonNode.Parse(ReadPayload())!.AsObject();

    internal static string RepoFile(string relativePath)
    {
        for (var directory = new DirectoryInfo(AppContext.BaseDirectory); directory is not null; directory = directory.Parent)
        {
            var candidate = Path.Combine(directory.FullName, relativePath.Replace('/', Path.DirectorySeparatorChar));
            if (File.Exists(candidate)) { return candidate; }
        }
        throw new InvalidOperationException("Shared protected cashier fixture is unavailable.");
    }
}
