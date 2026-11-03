using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace AurumPharma.Desktop.Core.Security;

/// <summary>
/// Strict credential data, not evidence of enrollment or login admission. Only the exact outer
/// payload digest may be compared with this bundle's independently authenticated checkpoint.
/// The embedded snapshot's digest is not a checkpoint proof for a bundle.
/// </summary>
public sealed class ProtectedCredentialBundle
{
    public const int MaximumPayloadBytes = 1_048_576;
    private readonly byte[] payloadDigest;
    private readonly Dictionary<Guid, CredentialBundleMaterial> materials;

    private ProtectedCredentialBundle(ProtectedCashierStateSnapshot snapshot,
        Dictionary<Guid, CredentialBundleMaterial> materials, byte[] digest)
    {
        Snapshot = snapshot;
        this.materials = materials;
        payloadDigest = digest.ToArray();
    }

    public ProtectedCashierStateSnapshot Snapshot { get; }
    public byte[] PayloadDigest => payloadDigest.ToArray();

    public static ProtectedCredentialBundle Parse(ReadOnlySpan<byte> raw)
    {
        if (raw.Length is < 1 or > MaximumPayloadBytes) { throw Failure(); }
        var bytes = raw.ToArray();
        try { return ParseBytes(bytes); }
        catch (Exception) { throw Failure(); }
        finally { CryptographicOperations.ZeroMemory(bytes); }
    }

    internal CredentialBundleMaterial ReadMaterial(Guid userId)
        => materials.TryGetValue(userId, out var material) ? material : throw Failure();

    public override string ToString() => nameof(ProtectedCredentialBundle);

    internal static CryptographicException Failure() => new("Protected credential bundle is unavailable.");

    private static ProtectedCredentialBundle ParseBytes(byte[] bytes)
    {
        using var document = JsonDocument.Parse(bytes, new JsonDocumentOptions
        {
            AllowDuplicateProperties = false,
            AllowTrailingCommas = false,
            CommentHandling = JsonCommentHandling.Disallow,
            MaxDepth = 32,
        });
        var root = document.RootElement;
        ValidateAtoms(root);
        Object(root, "schema_version", "cashier_state", "credential_materials");
        Version(root);
        var snapshotBytes = Encoding.UTF8.GetBytes(root.GetProperty("cashier_state").GetRawText());
        ProtectedCashierStateSnapshot snapshot;
        try { snapshot = ProtectedCashierStateSnapshot.Parse(snapshotBytes); }
        finally { CryptographicOperations.ZeroMemory(snapshotBytes); }
        var array = root.GetProperty("credential_materials");
        if (array.ValueKind != JsonValueKind.Array || array.GetArrayLength() != snapshot.Cashiers.Count)
        {
            throw Failure();
        }
        var result = new Dictionary<Guid, CredentialBundleMaterial>();
        var index = 0;
        foreach (var item in array.EnumerateArray())
        {
            Object(item, "user_id", "record", "pepper_envelope");
            var userId = Uuid(item, "user_id");
            var context = snapshot.Cashiers[index++].CredentialContext;
            if (context.Scope.UserId != userId) { throw Failure(); }
            var record = item.GetProperty("record");
            Object(record, "schema_version", "algorithm", "context", "verifier");
            Version(record);
            Equal(record, "algorithm", "bcrypt-hmac-sha384-v1");
            AssertContext(record.GetProperty("context"), context);
            var verifier = Text(record, "verifier");
            if (!VerifierPattern.IsMatch(verifier)) { throw Failure(); }
            var envelope = Envelope(item.GetProperty("pepper_envelope"), context.ProtectionKey);
            if (!result.TryAdd(userId, new CredentialBundleMaterial(userId, context, verifier, envelope)))
            {
                throw Failure();
            }
        }
        using var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        hash.AppendData("aurum:protected-credential-bundle:v1\0"u8);
        hash.AppendData(bytes);
        return new ProtectedCredentialBundle(snapshot, result, hash.GetHashAndReset());
    }

    private static readonly Regex VerifierPattern = new(
        "\\A\\$2b\\$12\\$[./A-Za-z0-9]{53}\\z", RegexOptions.CultureInvariant | RegexOptions.NonBacktracking);

    private static ProtectedLocalSecretEnvelope Envelope(JsonElement value, ProtectedCashierDeviceBinding key)
    {
        Object(value, "schema_version", "protection_key_id", "expected_spki_sha256", "ciphertext_base64");
        Version(value);
        if (Uuid(value, "protection_key_id") != key.KeyId) { throw Failure(); }
        Equal(value, "expected_spki_sha256", key.SpkiSha256);
        var encoded = Text(value, "ciphertext_base64");
        if (encoded.Length is < 344 or > 684) { throw Failure(); }
        var ciphertext = Convert.FromBase64String(encoded);
        try
        {
            if (ciphertext.Length is < 256 or > 512 || ciphertext.Length % 8 != 0 ||
                !string.Equals(Convert.ToBase64String(ciphertext), encoded, StringComparison.Ordinal))
            {
                throw Failure();
            }
            return new ProtectedLocalSecretEnvelope(1, key.KeyId, Convert.FromHexString(key.SpkiSha256), ciphertext);
        }
        finally { CryptographicOperations.ZeroMemory(ciphertext); }
    }

    private static void AssertContext(JsonElement value, ProtectedCashierCredentialContext expected)
    {
        Object(value, "schema_version", "audience", "scope", "local_credential", "device_binding", "protection_key");
        Version(value);
        Equal(value, "audience", expected.Audience);
        var scope = value.GetProperty("scope");
        Object(scope, "activation_id", "tenant_id", "branch_id", "edge_node_id", "register_id", "writer_epoch", "capability", "user_id");
        var actualScope = new ProtectedCashierWorkplaceScope(Uuid(scope, "activation_id"), Uuid(scope, "tenant_id"),
            Uuid(scope, "branch_id"), Uuid(scope, "edge_node_id"), Uuid(scope, "register_id"), Integer(scope, "writer_epoch"));
        Equal(scope, "capability", expected.Scope.Capability);
        if (actualScope != expected.Scope.Workplace || Uuid(scope, "user_id") != expected.Scope.UserId) { throw Failure(); }
        var reference = value.GetProperty("local_credential");
        Object(reference, "reference_id", "revision");
        if (Uuid(reference, "reference_id") != expected.LocalCredential.ReferenceId ||
            Integer(reference, "revision") != expected.LocalCredential.Revision) { throw Failure(); }
        AssertKey(value.GetProperty("device_binding"), expected.DeviceBinding);
        AssertKey(value.GetProperty("protection_key"), expected.ProtectionKey);
    }

    private static void AssertKey(JsonElement value, ProtectedCashierDeviceBinding expected)
    {
        Object(value, "method", "key_id", "spki_sha256");
        Equal(value, "method", "tpm2");
        if (Uuid(value, "key_id") != expected.KeyId) { throw Failure(); }
        Equal(value, "spki_sha256", expected.SpkiSha256);
    }

    private static void Object(JsonElement value, params string[] names)
    {
        if (value.ValueKind != JsonValueKind.Object) { throw Failure(); }
        var actual = value.EnumerateObject().Select(property => property.Name).ToArray();
        if (actual.Length != names.Length || actual.Any(name => !names.Contains(name, StringComparer.Ordinal))) { throw Failure(); }
    }

    private static string Text(JsonElement parent, string name)
    {
        var value = parent.GetProperty(name);
        if (value.ValueKind != JsonValueKind.String) { throw Failure(); }
        return value.GetString() ?? throw Failure();
    }

    private static Guid Uuid(JsonElement parent, string name)
    {
        var text = Text(parent, name);
        if (text.Length != 36 || !Guid.TryParseExact(text, "D", out var id) || !ProtectedCashierSnapshotParser.IsUuid4(id))
        {
            throw Failure();
        }
        return id;
    }

    private static long Integer(JsonElement parent, string name)
    {
        var value = parent.GetProperty(name);
        if (value.ValueKind != JsonValueKind.Number || !value.TryGetInt64(out var number)) { throw Failure(); }
        return number;
    }

    private static void Version(JsonElement parent)
    {
        if (Integer(parent, "schema_version") != 1) { throw Failure(); }
    }

    private static void Equal(JsonElement parent, string name, string expected)
    {
        if (!string.Equals(Text(parent, name), expected, StringComparison.Ordinal)) { throw Failure(); }
    }

    private static void ValidateAtoms(JsonElement value)
    {
        switch (value.ValueKind)
        {
            case JsonValueKind.Object:
                var seen = new HashSet<string>(StringComparer.Ordinal);
                foreach (var property in value.EnumerateObject())
                {
                    ValidateUnicode(property.Name);
                    if (!seen.Add(property.Name)) { throw Failure(); }
                    ValidateAtoms(property.Value);
                }
                break;
            case JsonValueKind.Array:
                foreach (var item in value.EnumerateArray()) { ValidateAtoms(item); }
                break;
            case JsonValueKind.String:
                ValidateUnicode(value.GetString() ?? throw Failure());
                break;
            case JsonValueKind.Number:
                if (value.GetRawText().IndexOfAny(['.', 'e', 'E']) >= 0 || !value.TryGetInt64(out _)) { throw Failure(); }
                break;
            case JsonValueKind.Null:
                break;
            default:
                throw Failure();
        }
    }

    private static void ValidateUnicode(string value)
    {
        for (var index = 0; index < value.Length; index++)
        {
            if (char.IsHighSurrogate(value[index]))
            {
                if (++index >= value.Length || !char.IsLowSurrogate(value[index])) { throw Failure(); }
            }
            else if (char.IsLowSurrogate(value[index])) { throw Failure(); }
        }
    }
}

/// <summary>Encrypted pepper and verifier data, visible only inside the security assembly.</summary>
internal sealed class CredentialBundleMaterial
{
    internal CredentialBundleMaterial(Guid userId, ProtectedCashierCredentialContext context,
        string verifier, ProtectedLocalSecretEnvelope envelope)
    {
        UserId = userId;
        Context = context;
        Verifier = verifier;
        Envelope = envelope;
    }
    internal Guid UserId { get; }
    internal ProtectedCashierCredentialContext Context { get; }
    internal string Verifier { get; }
    internal ProtectedLocalSecretEnvelope Envelope { get; }
    public override string ToString() => nameof(CredentialBundleMaterial);
}
