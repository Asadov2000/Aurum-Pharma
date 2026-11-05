using System.Security.Cryptography;
using System.Text.Json.Nodes;
using AurumPharma.Desktop.Core.Security;

namespace AurumPharma.Desktop.Core.Tests;

public sealed class WindowsTpmCashierStateSourceTests
{
    [Fact]
    public void ExistingFilesProvideCheckpointVerifiedCashierMetadata()
    {
        using var fixture = new SourceFiles();
        using var source = fixture.Open();
        var result = source.ReadCashierState(fixture.UserId);
        Assert.Equal(7UL, result.Counter);
        Assert.Equal(fixture.UserId, result.Cashier.Authorization.Scope.UserId);
        Assert.Equal(fixture.Expected.PolicyFloor.Policy.Version, result.PolicyFloor.Policy.Version);
        Assert.Equal(fixture.Expected.PolicyFloor.Policy.TenantId, result.PolicyFloor.Policy.TenantId);
        Assert.Equal(fixture.Expected.WorkplaceSession!.SessionId, result.WorkplaceSession!.SessionId);
        Assert.Equal(2, fixture.Counter.Reads);
        Assert.Equal(0, fixture.Counter.Writes);
        // The snapshot handle is closed before either verified result becomes visible.
        using var exclusive = new FileStream(fixture.SnapshotPath, FileMode.Open, FileAccess.ReadWrite, FileShare.None);
    }

    [Fact]
    public void WhitespaceChangesAreNotNormalizedBeforeCheckpointComparison()
    {
        using var fixture = new SourceFiles();
        File.AppendAllText(fixture.SnapshotPath, " ");
        using var source = fixture.Open();
        AssertDenied(() => source.ReadSnapshot());
        var reads = fixture.Counter.Reads;
        AssertDenied(() => source.ReadSnapshot());
        Assert.Equal(reads, fixture.Counter.Reads);
        Assert.True(fixture.Counter.Disposed);
    }

    [Fact]
    public void RestoredSessionBytesCannotHideBehindAnUnchangedGuard()
    {
        using var fixture = new SourceFiles();
        var changed = JsonNode.Parse(fixture.Raw)!.AsObject();
        changed["workplace_session"]!["session_id"] = Guid.NewGuid().ToString("D");
        File.WriteAllText(fixture.SnapshotPath, changed.ToJsonString());
        using var source = fixture.Open();
        AssertDenied(() => source.ReadSnapshot());
        Assert.Equal(0, fixture.Counter.Writes);
    }

    [Fact]
    public void RestoringOldSnapshotAfterCheckpointAdvanceDenies()
    {
        using var fixture = new SourceFiles();
        var next = SHA256.HashData("a later protected admission snapshot"u8);
        fixture.Counter.Value = 8;
        fixture.ReplaceCheckpoint(8, next);
        using var source = fixture.Open();
        AssertDenied(() => source.ReadSnapshot());
        Assert.Equal(0, fixture.Counter.Writes);
    }

    [Theory]
    [InlineData("tenant")]
    [InlineData("device")]
    [InlineData("context")]
    public void IndependentlyExpectedBindingCannotBeAdoptedFromSnapshot(string changed)
    {
        using var fixture = new SourceFiles();
        var scope = fixture.Expected.Scope;
        var expectedScope = changed == "tenant"
            ? new ProtectedCashierWorkplaceScope(scope.ActivationId, Guid.NewGuid(), scope.BranchId,
                scope.EdgeNodeId, scope.RegisterId, scope.WriterEpoch)
            : scope;
        var expectedDevice = changed == "device"
            ? new ProtectedCashierDeviceBinding(Guid.NewGuid(), new string('a', 64))
            : fixture.Expected.DeviceBinding;
        var context = changed == "context" ? new byte[32] : fixture.Expected.CheckpointContextSha256;
        using var source = fixture.Open(expectedScope: expectedScope, expectedDevice: expectedDevice, context: context);
        AssertDenied(() => source.ReadSnapshot());
        Assert.Equal(0, fixture.Counter.Reads);
    }

    [Theory]
    [InlineData("revoked")]
    [InlineData("locally-blocked")]
    [InlineData("new-floor")]
    [InlineData("tenant-suspended")]
    [InlineData("readonly")]
    [InlineData("blocked-pos")]
    [InlineData("blocked-inventory")]
    public void SealedBusinessDenialNeverSelectsCashierOrPoisonsReadableMetadata(string restriction)
    {
        using var fixture = new SourceFiles(payload =>
        {
            var policy = payload["policy_floor"]!["policy"]!;
            policy["expires_at"] = null;
            if (restriction is "revoked" or "locally-blocked")
                payload["cashiers"]![0]!["authorization"]!["status"] = restriction;
            else if (restriction == "new-floor") policy["version"] = policy["version"]!.GetValue<long>() + 1;
            else if (restriction == "tenant-suspended") policy["tenant_status"] = "suspended";
            else if (restriction == "readonly") policy["mode"] = "readonly";
            else policy["blocked_modules"] = new JsonArray(restriction == "blocked-pos" ? "pos" : "inventory");
        });
        using var source = fixture.Open();
        AssertDenied(() => source.ReadCashierState(fixture.UserId));
        Assert.Equal(7UL, source.ReadSnapshot().Counter);
        Assert.False(fixture.Counter.Disposed);
        Assert.Equal(0, fixture.Counter.Writes);
    }

    [Fact]
    public void UnknownUserDeniesWithoutTurningMetadataIntoALoginResult()
    {
        using var fixture = new SourceFiles();
        using var source = fixture.Open();
        AssertDenied(() => source.ReadCashierState(Guid.NewGuid()));
        AssertDenied(() => source.ReadCashierState(Guid.Empty));
        Assert.Equal(fixture.UserId, source.ReadCashierState(fixture.UserId).Cashier.Authorization.Scope.UserId);
        Assert.Equal(nameof(CheckpointVerifiedCashierState), source.ReadCashierState(fixture.UserId).ToString());
    }

    [Fact]
    public void ChangedHardwareBetweenReadsCannotPublishEarlierMetadata()
    {
        using var fixture = new SourceFiles();
        fixture.Counter.BeforeRead = count => { if (count == 2) fixture.Counter.Value++; };
        using var source = fixture.Open();
        AssertDenied(() => source.ReadSnapshot());
        Assert.Equal(2, fixture.Counter.Reads);
        Assert.True(fixture.Counter.Disposed);
    }

    [Fact]
    public void TwoIndividuallyValidDifferentCheckpointDigestsCannotPublishMetadata()
    {
        using var fixture = new SourceFiles();
        var memory = new MemoryCheckpoint(fixture.CheckpointFrame(7, fixture.Expected.PayloadDigest));
        memory.AfterClose = count =>
        {
            if (count == 1) memory.Frame = fixture.CheckpointFrame(7, SHA256.HashData("a changed checkpoint"u8));
        };
        using var source = fixture.Open(checkpointStorage: memory);
        AssertDenied(() => source.ReadSnapshot());
        Assert.Equal(2, fixture.Counter.Reads);
        Assert.Equal(2, memory.Closed);
    }

    [Theory]
    [InlineData("open")]
    [InlineData("read")]
    [InlineData("close")]
    public void SnapshotIoFailureIsGenericAndIrreversiblyClosesSource(string failure)
    {
        using var fixture = new SourceFiles();
        var files = new FaultSnapshot(fixture.Raw, failure);
        using var source = fixture.Open(snapshotStorage: files);
        AssertDenied(() => source.ReadSnapshot());
        var reads = fixture.Counter.Reads;
        AssertDenied(() => source.ReadSnapshot());
        Assert.Equal(reads, fixture.Counter.Reads);
        Assert.True(fixture.Counter.Disposed);
        Assert.Equal(failure == "close" ? 2 : 0, reads);
    }

    [Theory]
    [InlineData(0)]
    [InlineData(1_048_577)]
    public void EmptyOrOversizedFileDeniesBeforeCheckpointAccess(int length)
    {
        using var fixture = new SourceFiles();
        File.WriteAllBytes(fixture.SnapshotPath, new byte[length]);
        using var source = fixture.Open();
        AssertDenied(() => source.ReadSnapshot());
        Assert.Equal(0, fixture.Counter.Reads);
    }

    [Fact]
    public void MissingFileIsNotInitializedAndExclusiveOwnershipIsRespected()
    {
        using var fixture = new SourceFiles();
        using (var source = fixture.Open())
        using (var held = new FileStream(fixture.SnapshotPath, FileMode.Open, FileAccess.Read, FileShare.None))
        {
            AssertDenied(() => source.ReadSnapshot());
            Assert.Equal(0, fixture.Counter.Reads);
        }
        File.Delete(fixture.SnapshotPath);
        Assert.Throws<FileNotFoundException>(() => new ExistingProtectedCashierSnapshotStorage(fixture.SnapshotPath));
        Assert.False(File.Exists(fixture.SnapshotPath));
    }

    private static void AssertDenied(Action action)
    {
        var error = Assert.Throws<CryptographicException>(action);
        Assert.Equal("Protected cashier state is unavailable.", error.Message);
        Assert.Null(error.InnerException);
    }

    private sealed class SourceFiles : IDisposable
    {
        private readonly string directory;
        private readonly byte[] binding = SHA256.HashData("fictional independent checkpoint binding"u8);
        private readonly byte[] key = SHA256.HashData("fictional independent checkpoint key"u8);
        public string SnapshotPath { get; }
        private string CheckpointPath { get; }
        public ProtectedCashierStateSnapshot Expected { get; }
        public byte[] Raw { get; }
        public Guid UserId => Expected.Cashiers[0].Authorization.Scope.UserId;
        public CounterHandle Counter { get; } = new();

        public SourceFiles(Action<JsonObject>? prepare = null)
        {
            var golden = ProtectedCashierSnapshotFixture.ReadPayload();
            Expected = ProtectedCashierStateSnapshot.Parse(golden);
            if (prepare is not null)
            {
                var data = JsonNode.Parse(golden)!.AsObject();
                prepare(data);
                Raw = System.Text.Encoding.UTF8.GetBytes(data.ToJsonString());
            }
            else Raw = golden;
            directory = Path.Combine(AppContext.BaseDirectory, "cashier-source-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(directory);
            SnapshotPath = Path.Combine(directory, "cashiers.json");
            CheckpointPath = Path.Combine(directory, "state.bin");
            File.WriteAllBytes(SnapshotPath, Raw);
            var digest = ProtectedCashierStateSnapshot.Parse(Raw).PayloadDigest;
            ReplaceCheckpoint(7, digest);
        }

        public byte[] CheckpointFrame(ulong generation, byte[] digest)
            => ProtectedLocalStateCheckpointCodec.CreateFrame(binding, generation, digest, key);
        public void ReplaceCheckpoint(ulong generation, byte[] digest)
            => File.WriteAllBytes(CheckpointPath, CheckpointFrame(generation, digest));

        public WindowsTpmCashierStateSource Open(
            IProtectedCashierSnapshotStorage? snapshotStorage = null,
            IProtectedStateCheckpointStorage? checkpointStorage = null,
            ProtectedCashierWorkplaceScope? expectedScope = null,
            ProtectedCashierDeviceBinding? expectedDevice = null,
            byte[]? context = null)
        {
            var checkpoint = new WindowsTpmProtectedStateStore(Counter,
                checkpointStorage ?? new ExistingProtectedStateCheckpointFileStorage(CheckpointPath), binding, key);
            return new WindowsTpmCashierStateSource(checkpoint,
                snapshotStorage ?? new ExistingProtectedCashierSnapshotStorage(SnapshotPath),
                expectedScope ?? Expected.Scope, expectedDevice ?? Expected.DeviceBinding,
                context ?? Expected.CheckpointContextSha256);
        }

        public void Dispose()
        {
            File.Delete(SnapshotPath);
            File.Delete(CheckpointPath);
            Directory.Delete(directory, recursive: false);
            CryptographicOperations.ZeroMemory(key);
        }
    }

    private sealed class CounterHandle : IProtectedStateCounter
    {
        public ulong Value = 7;
        public int Reads;
        public int Writes;
        public bool Disposed;
        public Action<int>? BeforeRead;
        public ulong ReadCurrent()
        {
            ObjectDisposedException.ThrowIf(Disposed, this);
            Reads++;
            BeforeRead?.Invoke(Reads);
            return Value;
        }
        public ulong Advance(ulong expected) { Writes++; throw new InvalidOperationException("Reader cannot advance hardware."); }
        public void Dispose() => Disposed = true;
    }

    private sealed class FaultSnapshot(byte[] raw, string failure) : IProtectedCashierSnapshotStorage
    {
        public IProtectedCashierSnapshotFile OpenExclusive()
        {
            if (failure == "open") throw new IOException("sensitive fixture diagnostic");
            return new Opened(raw, failure);
        }
        private sealed class Opened(byte[] raw, string failure) : IProtectedCashierSnapshotFile
        {
            public byte[] ReadBounded(int maximumBytes)
            {
                if (failure == "read") throw new IOException("sensitive fixture diagnostic");
                return raw.ToArray();
            }
            public void Dispose() { if (failure == "close") throw new IOException("sensitive fixture diagnostic"); }
        }
    }

    private sealed class MemoryCheckpoint(byte[] initial) : IProtectedStateCheckpointStorage
    {
        public byte[] Frame = initial;
        public int Closed;
        public Action<int>? AfterClose;
        public IProtectedStateCheckpointFile OpenExclusive() => new Opened(this);
        private sealed class Opened(MemoryCheckpoint owner) : IProtectedStateCheckpointFile
        {
            public byte[] ReadExact(int length) => owner.Frame.ToArray();
            public void WriteAndFlush(ReadOnlySpan<byte> frame) => throw new InvalidOperationException("Read-only fixture.");
            public void Dispose() => owner.AfterClose?.Invoke(++owner.Closed);
        }
    }
}
