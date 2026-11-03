using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using AurumPharma.Desktop.Core.Security;

namespace AurumPharma.Desktop.Core.Tests;

public sealed class WindowsTpmCredentialMaterialSourceTests
{
    [Fact]
    public void RealFilesAndRsaUnwrapOnlyAfterCheckpointVerificationAndCloseBeforePublication()
    {
        using var files = new SourceFiles();
        using var source = files.Open();
        using var material = source.ReadMaterial(files.UserId);
        Assert.Equal(7UL, material.Counter);
        Assert.Equal(files.Expected.PayloadDigest, material.BundleDigest);
        Assert.Equal(files.UserId, material.Cashier.Authorization.Scope.UserId);
        Assert.Equal(files.Expected.Snapshot.WorkplaceSession!.SessionId, material.AdmissionMetadata.WorkplaceSession!.SessionId);
        Assert.True(material.Use((pepper, verifier) => pepper.SequenceEqual(files.Pepper) && verifier == files.Verifier));
        Assert.Equal(2, files.Counter.Reads);
        Assert.Equal(0, files.Counter.Writes);
        Assert.True(files.Unprotector!.Disposed);
        using var exclusive = new FileStream(files.BundlePath, FileMode.Open, FileAccess.ReadWrite, FileShare.None);
        Assert.Equal(nameof(CheckpointVerifiedCredentialMaterial), material.ToString());
        Assert.Equal(nameof(WindowsTpmCredentialMaterialSource), source.ToString());
        material.BundleDigest[0] ^= 1;
        Assert.Equal(files.Expected.PayloadDigest, material.BundleDigest);
    }

    [Theory]
    [InlineData("whitespace")]
    [InlineData("verifier-and-known-pepper")]
    [InlineData("workplace-session")]
    public void PublicKeyEncryptionCannotAuthorizeSubstitution(string changed)
    {
        using var files = new SourceFiles();
        if (changed == "whitespace") File.AppendAllText(files.BundlePath, " ");
        else
        {
            var data = JsonNode.Parse(files.Raw)!.AsObject();
            if (changed == "workplace-session")
                data["cashier_state"]!["workplace_session"]!["session_id"] = Guid.NewGuid().ToString("D");
            else
            {
                var entry = data["credential_materials"]![0]!;
                entry["record"]!["verifier"] = "$2b$12$" + new string('B', 53);
                var envelope = files.Codec.Protect(new byte[32], files.Expected.Snapshot.Cashiers[0].CredentialContext.ContextDigestSha256);
                entry["pepper_envelope"]!["ciphertext_base64"] = Convert.ToBase64String(envelope.Ciphertext);
            }
            File.WriteAllText(files.BundlePath, data.ToJsonString());
        }
        using var source = files.Open();
        Denied(() => source.ReadMaterial(files.UserId));
        Assert.Null(files.Unprotector);
        Assert.Equal(0, files.Counter.Writes);
        Assert.True(files.Counter.Disposed);
    }

    [Theory]
    [InlineData("tenant")]
    [InlineData("device")]
    [InlineData("checkpoint-context")]
    public void TrustedExpectedBindingCannotBeAdoptedFromTheFile(string changed)
    {
        using var files = new SourceFiles();
        var scope = files.Expected.Snapshot.Scope;
        using var source = files.Open(expectedScope: changed == "tenant"
            ? new ProtectedCashierWorkplaceScope(scope.ActivationId, Guid.NewGuid(), scope.BranchId, scope.EdgeNodeId, scope.RegisterId, scope.WriterEpoch)
            : scope, expectedDevice: changed == "device"
            ? new ProtectedCashierDeviceBinding(Guid.NewGuid(), new string('a', 64)) : null,
            context: changed == "checkpoint-context" ? new byte[32] : null);
        Denied(() => source.ReadMaterial(files.UserId));
        Assert.Equal(0, files.Counter.Reads);
        Assert.Null(files.Unprotector);
    }

    [Fact]
    public void RestoredBundleCannotUseAnAdvancedCheckpoint()
    {
        using var files = new SourceFiles();
        files.Counter.Value++;
        files.ReplaceCheckpoint(8, SHA256.HashData("later credential revision"u8));
        using var source = files.Open();
        Denied(() => source.ReadMaterial(files.UserId));
        Assert.Null(files.Unprotector);
    }

    [Theory]
    [InlineData("revoked")]
    [InlineData("new-floor")]
    [InlineData("suspended")]
    [InlineData("readonly")]
    [InlineData("blocked-pos")]
    [InlineData("blocked-inventory")]
    public void AuthenticatedButIneligibleCashierNeverUnlocksPepper(string restriction)
    {
        using var files = new SourceFiles(data =>
        {
            var state = data["cashier_state"]!;
            var policy = state["policy_floor"]!["policy"]!;
            policy["expires_at"] = null;
            if (restriction == "revoked") state["cashiers"]![0]!["authorization"]!["status"] = "revoked";
            else if (restriction == "new-floor") policy["version"] = policy["version"]!.GetValue<long>() + 1;
            else if (restriction == "suspended") policy["tenant_status"] = "suspended";
            else if (restriction == "readonly") policy["mode"] = "readonly";
            else policy["blocked_modules"] = new JsonArray(restriction == "blocked-pos" ? "pos" : "inventory");
        });
        using var source = files.Open();
        Denied(() => source.ReadMaterial(files.UserId));
        Assert.Null(files.Unprotector);
    }

    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public void MissingOrInvalidUserNeverUnlocksAnyOtherCashier(bool empty)
    {
        using var files = new SourceFiles();
        using var source = files.Open();
        Denied(() => source.ReadMaterial(empty ? Guid.Empty : Guid.NewGuid()));
        Assert.Null(files.Unprotector);
    }

    [Theory]
    [InlineData("counter")]
    [InlineData("digest")]
    public void ChangedCheckpointDuringUnwrapClearsPlaintextAndNeverPublishes(string change)
    {
        using var files = new SourceFiles();
        files.AfterDecrypt = () =>
        {
            if (change == "counter") files.Counter.Value++;
            else files.ReplaceCheckpoint(7, SHA256.HashData("different accepted bundle"u8));
        };
        using var source = files.Open();
        Denied(() => source.ReadMaterial(files.UserId));
        Assert.All(files.Unprotector!.ReturnedPepper!, value => Assert.Equal(0, value));
        Assert.True(files.Unprotector.Disposed);
        Assert.True(files.Counter.Disposed);
    }

    [Theory]
    [InlineData("key-open")]
    [InlineData("decrypt")]
    [InlineData("key-close")]
    [InlineData("wrong-length")]
    [InlineData("file-close")]
    public void FailuresAfterCheckpointNeverLeakPlaintextAndLatchSource(string failure)
    {
        using var files = new SourceFiles();
        files.Failure = failure;
        using var source = files.Open(storage: failure == "file-close" ? new FaultStorage(files.Raw, failure) : null);
        Denied(() => source.ReadMaterial(files.UserId));
        var reads = files.Counter.Reads;
        Denied(() => source.ReadMaterial(files.UserId));
        Assert.Equal(reads, files.Counter.Reads);
        Assert.True(files.Counter.Disposed);
        if (files.Unprotector?.ReturnedPepper is { } plaintext)
            Assert.All(plaintext, value => Assert.Equal(0, value));
    }

    [Theory]
    [InlineData("file-open")]
    [InlineData("file-read")]
    public void InputFailuresDenyBeforeCheckpointAndSecretAccess(string failure)
    {
        using var files = new SourceFiles();
        using var source = files.Open(storage: new FaultStorage(files.Raw, failure));
        Denied(() => source.ReadMaterial(files.UserId));
        Assert.Equal(0, files.Counter.Reads);
        Assert.Null(files.Unprotector);
    }

    [Fact]
    public void DisposalClearsTheOwnedPlaintextAndPreventsReuse()
    {
        using var files = new SourceFiles();
        using var source = files.Open();
        var material = source.ReadMaterial(files.UserId);
        Assert.Equal(files.Pepper, files.Unprotector!.ReturnedPepper);
        material.Dispose();
        Assert.All(files.Unprotector.ReturnedPepper!, value => Assert.Equal(0, value));
        Denied(() => material.Use((pepper, _) => pepper.Length));
        material.Dispose();
        source.Dispose();
        Denied(() => source.ReadMaterial(files.UserId));
    }

    [Fact]
    public void MissingFileAndSnapshotV1CannotBecomeCredentialBundles()
    {
        using var files = new SourceFiles();
        File.WriteAllBytes(files.BundlePath, ProtectedCashierSnapshotFixture.ReadPayload());
        using (var source = files.Open()) Denied(() => source.ReadMaterial(files.UserId));
        Assert.Null(files.Unprotector);
        File.Delete(files.BundlePath);
        Assert.Throws<FileNotFoundException>(() => new ExistingProtectedCashierSnapshotStorage(files.BundlePath));
        Assert.False(File.Exists(files.BundlePath));
    }

    private static void Denied(Action action)
    {
        var error = Assert.Throws<CryptographicException>(action);
        Assert.Equal("Protected local credential material is unavailable", error.Message);
        Assert.Null(error.InnerException);
    }

    private sealed class SourceFiles : IDisposable
    {
        private readonly string directory;
        private readonly byte[] binding = SHA256.HashData("fictional credential checkpoint binding"u8);
        private readonly byte[] key = SHA256.HashData("fictional credential checkpoint authentication"u8);
        private readonly RSA rsa = RSA.Create(2048);
        private readonly string checkpointPath;
        public byte[] Pepper { get; } = Enumerable.Range(1, 32).Select(value => (byte)value).ToArray();
        public string Verifier { get; } = "$2b$12$" + new string('A', 53);
        public FixedPurposeSecretCodec Codec { get; }
        public string BundlePath { get; }
        public ProtectedCredentialBundle Expected { get; }
        public byte[] Raw { get; }
        public CounterHandle Counter { get; } = new();
        public Guid UserId => Expected.Snapshot.Cashiers[0].Authorization.Scope.UserId;
        public SecretHandle? Unprotector;
        public Action? AfterDecrypt;
        public string? Failure;

        public SourceFiles(Action<JsonObject>? prepare = null)
        {
            var state = JsonNode.Parse(ProtectedCashierSnapshotFixture.ReadPayload())!.AsObject();
            var original = ProtectedCashierStateSnapshot.Parse(ProtectedCashierSnapshotFixture.ReadPayload()).Cashiers[0].CredentialContext;
            var keyId = Guid.NewGuid();
            var spki = SHA256.HashData(rsa.ExportSubjectPublicKeyInfo());
            var protection = new ProtectedCashierDeviceBinding(keyId, Convert.ToHexString(spki).ToLowerInvariant());
            var context = new ProtectedCashierCredentialContext(original.Scope, original.LocalCredential, original.DeviceBinding, protection);
            var cashier = state["cashiers"]![0]!;
            var wireContext = cashier["credential_context"]!;
            wireContext["protection_key"]!["key_id"] = keyId.ToString("D");
            wireContext["protection_key"]!["spki_sha256"] = protection.SpkiSha256;
            cashier["guard"]!["context_digest"] = Convert.ToHexString(context.ContextDigestSha256).ToLowerInvariant();
            Codec = new FixedPurposeSecretCodec(rsa, keyId, spki);
            var envelope = Codec.Protect(Pepper, context.ContextDigestSha256);
            var root = new JsonObject
            {
                ["schema_version"] = 1,
                ["cashier_state"] = state,
                ["credential_materials"] = new JsonArray(new JsonObject
                {
                    ["user_id"] = original.Scope.UserId.ToString("D"),
                    ["record"] = new JsonObject
                    {
                        ["schema_version"] = 1, ["algorithm"] = "bcrypt-hmac-sha384-v1",
                        ["context"] = wireContext.DeepClone(), ["verifier"] = Verifier
                    },
                    ["pepper_envelope"] = new JsonObject
                    {
                        ["schema_version"] = 1, ["protection_key_id"] = keyId.ToString("D"),
                        ["expected_spki_sha256"] = protection.SpkiSha256,
                        ["ciphertext_base64"] = Convert.ToBase64String(envelope.Ciphertext)
                    }
                })
            };
            prepare?.Invoke(root);
            Raw = Encoding.UTF8.GetBytes(root.ToJsonString());
            Expected = ProtectedCredentialBundle.Parse(Raw);
            directory = Path.Combine(AppContext.BaseDirectory, "credential-source-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(directory);
            BundlePath = Path.Combine(directory, "credentials.json");
            checkpointPath = Path.Combine(directory, "checkpoint.bin");
            File.WriteAllBytes(BundlePath, Raw);
            ReplaceCheckpoint(7, Expected.PayloadDigest);
        }

        public void ReplaceCheckpoint(ulong generation, byte[] digest)
            => File.WriteAllBytes(checkpointPath, ProtectedLocalStateCheckpointCodec.CreateFrame(binding, generation, digest, key));

        public WindowsTpmCredentialMaterialSource Open(IProtectedCashierSnapshotStorage? storage = null,
            ProtectedCashierWorkplaceScope? expectedScope = null, ProtectedCashierDeviceBinding? expectedDevice = null, byte[]? context = null)
        {
            var checkpoint = new WindowsTpmProtectedStateStore(Counter,
                new ExistingProtectedStateCheckpointFileStorage(checkpointPath), binding, key);
            return new WindowsTpmCredentialMaterialSource(checkpoint,
                storage ?? new ExistingProtectedCashierSnapshotStorage(BundlePath),
                expectedScope ?? Expected.Snapshot.Scope, expectedDevice ?? Expected.Snapshot.DeviceBinding,
                context ?? Expected.Snapshot.CheckpointContextSha256, expected =>
                {
                    Assert.Equal(Expected.Snapshot.Cashiers[0].CredentialContext, expected);
                    if (Failure == "key-open") throw new CryptographicException("sensitive fake key diagnostic");
                    Unprotector = new SecretHandle(this);
                    return Unprotector;
                });
        }

        public void Dispose()
        {
            File.Delete(BundlePath);
            File.Delete(checkpointPath);
            Directory.Delete(directory, recursive: false);
            Codec.Dispose();
            rsa.Dispose();
            CryptographicOperations.ZeroMemory(Pepper);
            CryptographicOperations.ZeroMemory(key);
        }
    }

    private sealed class SecretHandle(SourceFiles owner) : ILocalCredentialSecretUnprotector
    {
        public byte[]? ReturnedPepper;
        public bool Disposed;
        public byte[] Unprotect(ProtectedLocalSecretEnvelope envelope, ReadOnlySpan<byte> contextSha256)
        {
            if (owner.Failure == "decrypt") throw new CryptographicException("sensitive fake decrypt diagnostic");
            ReturnedPepper = owner.Failure == "wrong-length" ? new byte[31] : owner.Codec.Unprotect(envelope, contextSha256);
            owner.AfterDecrypt?.Invoke();
            return ReturnedPepper;
        }
        public void Dispose()
        {
            Disposed = true;
            if (owner.Failure == "key-close") throw new IOException("sensitive fake close diagnostic");
        }
    }

    private sealed class CounterHandle : IProtectedStateCounter
    {
        public ulong Value = 7;
        public int Reads;
        public int Writes;
        public bool Disposed;
        public ulong ReadCurrent() { ObjectDisposedException.ThrowIf(Disposed, this); Reads++; return Value; }
        public ulong Advance(ulong expected) { Writes++; throw new InvalidOperationException("Read-only source"); }
        public void Dispose() => Disposed = true;
    }

    private sealed class FaultStorage(byte[] raw, string failure) : IProtectedCashierSnapshotStorage
    {
        public IProtectedCashierSnapshotFile OpenExclusive()
        {
            if (failure == "file-open") throw new IOException("sensitive fake open diagnostic");
            return new Opened(raw, failure);
        }
        private sealed class Opened(byte[] raw, string failure) : IProtectedCashierSnapshotFile
        {
            public byte[] ReadBounded(int maximumBytes)
            {
                if (failure == "file-read") throw new IOException("sensitive fake read diagnostic");
                return raw.ToArray();
            }
            public void Dispose() { if (failure == "file-close") throw new IOException("sensitive fake close diagnostic"); }
        }
    }
}
