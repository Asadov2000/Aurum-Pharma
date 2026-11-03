using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using AurumPharma.Desktop.Core.Security;

namespace AurumPharma.Desktop.Core.Tests;

public sealed class ProtectedCredentialBundleTests
{
    [Fact]
    public void SharedPythonFixtureBindsExactOuterBytesAndPreservesEmbeddedSnapshot()
    {
        var bytes = CredentialBundleFixture.ReadPayload();
        var bundle = ProtectedCredentialBundle.Parse(bytes);
        var expected = File.ReadAllText(ProtectedCashierSnapshotFixture.RepoFile(
            "docs/fixtures/protected-credential-bundle-v1.sha256")).Trim();
        Assert.Equal(expected, Convert.ToHexStringLower(bundle.PayloadDigest));
        var cashier = bundle.Snapshot.Cashiers.Single();
        var material = bundle.ReadMaterial(cashier.Authorization.Scope.UserId);
        Assert.Equal(cashier.CredentialContext, material.Context);
        Assert.Equal(cashier.CredentialContext.ProtectionKey.KeyId, material.Envelope.ProtectionKeyId);
        Assert.Equal(cashier.CredentialContext.ProtectionKey.SpkiSha256,
            Convert.ToHexStringLower(material.Envelope.ExpectedSpkiSha256));
        Assert.Equal(Enumerable.Range(0, 256).Select(value => (byte)value), material.Envelope.Ciphertext);
        Assert.StartsWith("$2b$12$", material.Verifier);
        Assert.Equal(60, material.Verifier.Length);
        Assert.Equal("sale.cash.complete", bundle.Snapshot.WorkplaceSession!.Confirmation!.Command);
        Assert.Equal(ProtectedCashierStateSnapshot.Parse(ProtectedCashierSnapshotFixture.ReadPayload()).PayloadDigest,
            bundle.Snapshot.PayloadDigest);
        Assert.NotEqual(bundle.Snapshot.PayloadDigest, bundle.PayloadDigest);
    }

    [Fact]
    public void HashUsesExactOuterBytesRatherThanReformattedOrInnerJson()
    {
        var first = Parse(CredentialBundleFixture.Json());
        var pretty = JsonSerializer.SerializeToUtf8Bytes(CredentialBundleFixture.Json(),
            new JsonSerializerOptions { WriteIndented = true });
        var second = ProtectedCredentialBundle.Parse(pretty);
        Assert.NotEqual(first.PayloadDigest, second.PayloadDigest);
        Assert.Equal(first.Snapshot.Cashiers[0].CredentialContext, second.Snapshot.Cashiers[0].CredentialContext);
        var json = CredentialBundleFixture.Json();
        CredentialBundleFixture.Set(json, "credential_materials/0/record/verifier", "$2b$12$" + new string('A', 53));
        var replaced = Parse(json);
        Assert.NotEqual(first.PayloadDigest, replaced.PayloadDigest);
        Assert.Equal(first.Snapshot.PayloadDigest, replaced.Snapshot.PayloadDigest);
    }

    [Fact]
    public void InputAndReturnedArraysCannotMutateParsedDataAndFormattingDoesNotRevealMaterial()
    {
        var bytes = CredentialBundleFixture.ReadPayload();
        var bundle = ProtectedCredentialBundle.Parse(bytes);
        var material = bundle.ReadMaterial(bundle.Snapshot.Cashiers[0].Authorization.Scope.UserId);
        var digest = bundle.PayloadDigest;
        var ciphertext = material.Envelope.Ciphertext;
        var spki = material.Envelope.ExpectedSpkiSha256;
        Array.Fill(bytes, (byte)0);
        bundle.PayloadDigest[0] ^= 1;
        material.Envelope.Ciphertext[0] ^= 1;
        material.Envelope.ExpectedSpkiSha256[0] ^= 1;
        Assert.Equal(digest, bundle.PayloadDigest);
        Assert.Equal(ciphertext, material.Envelope.Ciphertext);
        Assert.Equal(spki, material.Envelope.ExpectedSpkiSha256);
        object[] values = [bundle, material, material.Envelope];
        foreach (var value in values)
        {
            Assert.Equal(value.GetType().Name, value.ToString());
            Assert.DoesNotContain(material.Verifier, value.ToString()!);
            Assert.All(value.GetType().GetProperties(), property => Assert.Null(property.SetMethod));
        }
        Assert.DoesNotContain(typeof(ProtectedCredentialBundle).GetProperties(),
            property => property.Name.Contains("Material", StringComparison.Ordinal) ||
                property.Name.Contains("Verifier", StringComparison.Ordinal));
    }

    [Fact]
    public void EmptyCashierStateRequiresAnEmptyMaterialSet()
    {
        var json = CredentialBundleFixture.Json();
        CredentialBundleFixture.Set(json, "cashier_state/cashiers", new JsonArray());
        CredentialBundleFixture.Set(json, "cashier_state/workplace_session", null);
        CredentialBundleFixture.Set(json, "credential_materials", new JsonArray());
        Assert.Empty(Parse(json).Snapshot.Cashiers);
        CredentialBundleFixture.Set(json, "credential_materials", CredentialBundleFixture.Json()["credential_materials"]!.DeepClone());
        Unavailable(() => Parse(json));
    }

    [Theory]
    [InlineData(256)]
    [InlineData(264)]
    [InlineData(384)]
    [InlineData(504)]
    [InlineData(512)]
    public void SupportedBoundedCiphertextLengthsArePreserved(int length)
    {
        var json = CredentialBundleFixture.Json();
        var ciphertext = Enumerable.Range(0, length).Select(value => (byte)value).ToArray();
        CredentialBundleFixture.Set(json, "credential_materials/0/pepper_envelope/ciphertext_base64", Convert.ToBase64String(ciphertext));
        var bundle = Parse(json);
        Assert.Equal(ciphertext, bundle.ReadMaterial(bundle.Snapshot.Cashiers[0].Authorization.Scope.UserId).Envelope.Ciphertext);
    }

    [Theory]
    [InlineData(0)]
    [InlineData(248)]
    [InlineData(255)]
    [InlineData(257)]
    [InlineData(511)]
    [InlineData(513)]
    [InlineData(520)]
    public void InvalidCiphertextLengthsDenyBeforeUnprotect(int length)
    {
        var json = CredentialBundleFixture.Json();
        CredentialBundleFixture.Set(json, "credential_materials/0/pepper_envelope/ciphertext_base64", Convert.ToBase64String(new byte[length]));
        Unavailable(() => Parse(json));
    }

    [Theory]
    [InlineData(" ")]
    [InlineData("\n")]
    [InlineData("!")]
    [InlineData("missing-padding")]
    [InlineData("nonzero-pad-bits")]
    [InlineData("urlsafe")]
    public void NoncanonicalOrMalformedBase64Deny(string fault)
    {
        var json = CredentialBundleFixture.Json();
        var path = "credential_materials/0/pepper_envelope/ciphertext_base64";
        var encoded = CredentialBundleFixture.At(json, path).GetValue<string>();
        encoded = fault switch
        {
            "missing-padding" => encoded.TrimEnd('='),
            "nonzero-pad-bits" => encoded[..^3] + "x==",
            "urlsafe" => Convert.ToBase64String(Enumerable.Repeat((byte)255, 256).ToArray()).Replace('/', '_'),
            _ => encoded.Insert(16, fault),
        };
        CredentialBundleFixture.Set(json, path, encoded);
        Unavailable(() => Parse(json));
    }

    [Theory]
    [InlineData("schema_version", "2")]
    [InlineData("schema_version", "true")]
    [InlineData("schema_version", "null")]
    [InlineData("schema_version", "\"1\"")]
    [InlineData("credential_materials", "null")]
    [InlineData("credential_materials", "{}")]
    [InlineData("credential_materials/0/user_id", "\"00000008-0000-4000-8000-000000000001\"")]
    [InlineData("credential_materials/0/user_id", "\"00000007-0000-1000-8000-000000000001\"")]
    [InlineData("credential_materials/0/record/schema_version", "2")]
    [InlineData("credential_materials/0/record/algorithm", "\"bcrypt\"")]
    [InlineData("credential_materials/0/record/verifier", "\"**********\"")]
    [InlineData("credential_materials/0/record/verifier", "null")]
    [InlineData("credential_materials/0/record/context/schema_version", "2")]
    [InlineData("credential_materials/0/record/context/audience", "\"cloud\"")]
    [InlineData("credential_materials/0/record/context/scope/tenant_id", "\"00000020-0000-4000-8000-000000000001\"")]
    [InlineData("credential_materials/0/record/context/scope/branch_id", "\"00000020-0000-4000-8000-000000000001\"")]
    [InlineData("credential_materials/0/record/context/scope/activation_id", "\"00000020-0000-4000-8000-000000000001\"")]
    [InlineData("credential_materials/0/record/context/scope/edge_node_id", "\"00000020-0000-4000-8000-000000000001\"")]
    [InlineData("credential_materials/0/record/context/scope/register_id", "\"00000020-0000-4000-8000-000000000001\"")]
    [InlineData("credential_materials/0/record/context/scope/user_id", "\"00000020-0000-4000-8000-000000000001\"")]
    [InlineData("credential_materials/0/record/context/scope/writer_epoch", "7")]
    [InlineData("credential_materials/0/record/context/scope/capability", "\"other\"")]
    [InlineData("credential_materials/0/record/context/local_credential/reference_id", "\"00000020-0000-4000-8000-000000000001\"")]
    [InlineData("credential_materials/0/record/context/local_credential/revision", "4")]
    [InlineData("credential_materials/0/record/context/device_binding/method", "\"software\"")]
    [InlineData("credential_materials/0/record/context/device_binding/key_id", "\"00000020-0000-4000-8000-000000000001\"")]
    [InlineData("credential_materials/0/record/context/device_binding/spki_sha256", "\"abababababababababababababababababababababababababababababababab\"")]
    [InlineData("credential_materials/0/record/context/protection_key/key_id", "\"00000020-0000-4000-8000-000000000001\"")]
    [InlineData("credential_materials/0/record/context/protection_key/spki_sha256", "\"abababababababababababababababababababababababababababababababab\"")]
    [InlineData("credential_materials/0/pepper_envelope/schema_version", "2")]
    [InlineData("credential_materials/0/pepper_envelope/protection_key_id", "\"00000020-0000-4000-8000-000000000001\"")]
    [InlineData("credential_materials/0/pepper_envelope/expected_spki_sha256", "\"abababababababababababababababababababababababababababababababab\"")]
    public void ForeignBindingsInvalidSchemaAndMaskedVerifierDeny(string path, string replacement)
    {
        var json = CredentialBundleFixture.Json();
        // The device hash case deliberately changes only letter case: pins are lowercase exact strings.
        if (path.EndsWith("device_binding/spki_sha256", StringComparison.Ordinal))
        {
            replacement = "\"ABABABABABABABABABABABABABABABABABABABABABABABABABABABABABABABAB\"";
        }
        CredentialBundleFixture.Set(json, path, JsonNode.Parse(replacement));
        Unavailable(() => Parse(json));
    }

    [Theory]
    [InlineData("$2a$12$", 53)]
    [InlineData("$2b$04$", 53)]
    [InlineData("$2b$31$", 53)]
    [InlineData("$2b$12$", 52)]
    [InlineData("$2b$12$", 54)]
    public void UnsupportedBcryptFormatsCannotSelectExpensiveOrDifferentAlgorithms(string prefix, int suffixLength)
    {
        var json = CredentialBundleFixture.Json();
        CredentialBundleFixture.Set(json, "credential_materials/0/record/verifier", prefix + new string('A', suffixLength));
        Unavailable(() => Parse(json));
    }

    [Theory]
    [InlineData("")]
    [InlineData("credential_materials/0")]
    [InlineData("credential_materials/0/record")]
    [InlineData("credential_materials/0/record/context")]
    [InlineData("credential_materials/0/record/context/scope")]
    [InlineData("credential_materials/0/record/context/local_credential")]
    [InlineData("credential_materials/0/record/context/device_binding")]
    [InlineData("credential_materials/0/record/context/protection_key")]
    [InlineData("credential_materials/0/pepper_envelope")]
    public void MissingOrExtraMembersDenyAtEveryNewObjectBoundary(string path)
    {
        var extra = CredentialBundleFixture.Json();
        var extraObject = (path.Length == 0 ? extra : CredentialBundleFixture.At(extra, path)).AsObject();
        extraObject["unexpected"] = 1;
        Unavailable(() => Parse(extra));
        var missing = CredentialBundleFixture.Json();
        var missingObject = (path.Length == 0 ? missing : CredentialBundleFixture.At(missing, path)).AsObject();
        missingObject.Remove(missingObject.First().Key);
        Unavailable(() => Parse(missing));
    }

    [Fact]
    public void MissingDuplicateAndUnexpectedMaterialEntriesDeny()
    {
        var missing = CredentialBundleFixture.Json();
        missing["credential_materials"]!.AsArray().Clear();
        Unavailable(() => Parse(missing));
        var duplicate = CredentialBundleFixture.Json();
        duplicate["credential_materials"]!.AsArray().Add(duplicate["credential_materials"]![0]!.DeepClone());
        Unavailable(() => Parse(duplicate));
        var parsed = Parse(CredentialBundleFixture.Json());
        Unavailable(() => parsed.ReadMaterial(Guid.Empty));
        Unavailable(() => parsed.ReadMaterial(Guid.NewGuid()));
    }

    [Fact]
    public void MultipleCashiersHaveOneToOneOrderedMaterialWithoutCrossUserSelection()
    {
        var json = CredentialBundleFixture.Json();
        var first = Parse(json).Snapshot.Cashiers[0].CredentialContext;
        var otherId = Guid.Parse("0000000c-0000-4000-8000-000000000001");
        var otherContext = new ProtectedCashierCredentialContext(
            new ProtectedCashierUserScope(first.Scope.Workplace, otherId), first.LocalCredential,
            first.DeviceBinding, first.ProtectionKey);
        var other = json["cashier_state"]!["cashiers"]![0]!.DeepClone();
        CredentialBundleFixture.Set(other, "authorization/scope/user_id", otherId.ToString("D"));
        CredentialBundleFixture.Set(other, "credential_context/scope/user_id", otherId.ToString("D"));
        CredentialBundleFixture.Set(other, "guard/user_id", otherId.ToString("D"));
        CredentialBundleFixture.Set(other, "guard/context_digest", Convert.ToHexStringLower(otherContext.ContextDigestSha256));
        json["cashier_state"]!["cashiers"]!.AsArray().Add(other);
        var otherMaterial = json["credential_materials"]![0]!.DeepClone();
        CredentialBundleFixture.Set(otherMaterial, "user_id", otherId.ToString("D"));
        CredentialBundleFixture.Set(otherMaterial, "record/context", other["credential_context"]!.DeepClone());
        json["credential_materials"]!.AsArray().Add(otherMaterial);
        var bundle = Parse(json);
        Assert.Equal(first, bundle.ReadMaterial(first.Scope.UserId).Context);
        Assert.Equal(otherContext, bundle.ReadMaterial(otherId).Context);
        var materials = json["credential_materials"]!.AsArray();
        var saved = materials[0]!.DeepClone();
        materials[0] = materials[1]!.DeepClone();
        materials[1] = saved;
        Unavailable(() => Parse(json));
        materials[1] = materials[0]!.DeepClone();
        Unavailable(() => Parse(json));
    }

    [Fact]
    public void RevokedMetadataStillNeedsMatchingMaterialButDoesNotBecomeLoginAdmission()
    {
        var json = CredentialBundleFixture.Json();
        CredentialBundleFixture.Set(json, "cashier_state/cashiers/0/authorization/status", "revoked");
        var parsed = Parse(json);
        Assert.Equal("revoked", parsed.Snapshot.Cashiers[0].Authorization.Status);
        Assert.NotNull(parsed.ReadMaterial(parsed.Snapshot.Cashiers[0].Authorization.Scope.UserId));
    }

    [Theory]
    [InlineData("\"schema_version\":1", "\"schema_version\":1,\"schema_version\":1")]
    [InlineData("\"user_id\":", "\"user\\u005fid\":\"00000007-0000-4000-8000-000000000001\",\"user_id\":")]
    [InlineData("\"schema_version\":1", "\"schema_version\":1e0")]
    [InlineData("\"schema_version\":1", "\"schema_version\":1.0")]
    [InlineData("\"schema_version\":1", "\"schema_version\":NaN")]
    [InlineData("\"schema_version\":1", "\"schema_version\":9223372036854775808")]
    public void DuplicateEscapedDuplicateAndNonIntegerWireValuesDeny(string oldValue, string newValue)
    {
        var raw = Encoding.UTF8.GetString(CredentialBundleFixture.ReadPayload());
        Assert.Contains(oldValue, raw);
        Unavailable(() => ProtectedCredentialBundle.Parse(Encoding.UTF8.GetBytes(raw.Replace(oldValue, newValue, StringComparison.Ordinal))));
    }

    [Theory]
    [InlineData("\\ud800")]
    [InlineData("\\udc00")]
    [InlineData("\\ud800x")]
    [InlineData("\\ud800\\ud800")]
    public void UnpairedSurrogatesDenyWithoutLeakingMaterial(string escaped)
    {
        var raw = Encoding.UTF8.GetString(CredentialBundleFixture.ReadPayload());
        var changed = "{\"" + escaped + "\":null," + raw[1..];
        Unavailable(() => ProtectedCredentialBundle.Parse(Encoding.UTF8.GetBytes(changed)));
    }

    [Fact]
    public void InvalidUtf8DepthOverflowOversizeTruncationAndLegacyPayloadsDeny()
    {
        var raw = CredentialBundleFixture.ReadPayload();
        Unavailable(() => ProtectedCredentialBundle.Parse([]));
        Unavailable(() => ProtectedCredentialBundle.Parse(new byte[ProtectedCredentialBundle.MaximumPayloadBytes + 1]));
        Unavailable(() => ProtectedCredentialBundle.Parse(raw.AsSpan(0, raw.Length - 1)));
        Unavailable(() => ProtectedCredentialBundle.Parse(ProtectedCashierSnapshotFixture.ReadPayload()));
        Unavailable(() => ProtectedCredentialBundle.Parse([0x7b, 0x22, 0xff, 0x22, 0x3a, 0x31, 0x7d]));
        var deep = "{\"extra\":" + new string('[', 32) + "0" + new string(']', 32) + "," + Encoding.UTF8.GetString(raw)[1..];
        Unavailable(() => ProtectedCredentialBundle.Parse(Encoding.UTF8.GetBytes(deep)));
        Unavailable(() => ProtectedCredentialBundle.Parse(raw.Concat("{}"u8.ToArray()).ToArray()));
    }

    private static ProtectedCredentialBundle Parse(JsonNode json)
        => ProtectedCredentialBundle.Parse(JsonSerializer.SerializeToUtf8Bytes(json));

    private static void Unavailable(Action action)
    {
        var error = Assert.Throws<CryptographicException>(action);
        Assert.Equal("Protected credential bundle is unavailable.", error.Message);
        Assert.Null(error.InnerException);
    }
}

internal static class CredentialBundleFixture
{
    internal static byte[] ReadPayload() => File.ReadAllBytes(ProtectedCashierSnapshotFixture.RepoFile(
        "docs/fixtures/protected-credential-bundle-v1.json"));

    internal static JsonObject Json() => JsonNode.Parse(ReadPayload())!.AsObject();

    internal static JsonNode At(JsonNode root, string path)
    {
        foreach (var part in path.Split('/'))
        {
            root = root is JsonArray array ? array[int.Parse(part)]! : root[part]!;
        }
        return root;
    }

    internal static void Set(JsonNode root, string path, JsonNode? value)
    {
        var segments = path.Split('/');
        var parent = segments.Length == 1 ? root : At(root, string.Join('/', segments[..^1]));
        if (parent is JsonArray array) { array[int.Parse(segments[^1])] = value; }
        else { parent[segments[^1]] = value; }
    }
}
