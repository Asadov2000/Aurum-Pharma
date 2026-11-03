using System.Security.Cryptography;

namespace AurumPharma.Desktop.Core.Security;

/// <summary>Checkpoint-verified point-in-time data, never a command principal or login result.</summary>
public sealed class CheckpointVerifiedCashierSnapshot
{
    internal CheckpointVerifiedCashierSnapshot(ProtectedCashierStateSnapshot snapshot, ulong counter)
    {
        Snapshot = snapshot;
        Counter = counter;
    }
    public ProtectedCashierStateSnapshot Snapshot { get; }
    public ulong Counter { get; }
    public override string ToString() => nameof(CheckpointVerifiedCashierSnapshot);
}

/// <summary>
/// Protected admission metadata for one cashier. Password/pepper validity, current trusted UTC,
/// session deadlines and serialization through SQL commit remain separate mandatory checks.
/// </summary>
public sealed class CheckpointVerifiedCashierState
{
    internal CheckpointVerifiedCashierState(CheckpointVerifiedCashierSnapshot verified, ProtectedCashierRecord cashier)
    {
        Cashier = cashier;
        PolicyFloor = verified.Snapshot.PolicyFloor;
        ObservedTime = verified.Snapshot.ObservedTime;
        WorkplaceSession = verified.Snapshot.WorkplaceSession;
        Counter = verified.Counter;
    }
    public ProtectedCashierRecord Cashier { get; }
    public ProtectedCashierPolicyFloor PolicyFloor { get; }
    public ProtectedCashierObservedTime ObservedTime { get; }
    public ProtectedCashierSession? WorkplaceSession { get; }
    public ulong Counter { get; }
    public override string ToString() => nameof(CheckpointVerifiedCashierState);
}

/// <summary>
/// Reads an existing service-owned snapshot against the independently bound TPM checkpoint.
/// An authenticated provisioning/acceptance owner must already have established both files,
/// enrollment, directory ACLs and exclusive index ownership. No inbox row or supplied lease can
/// initialize this source. Reading data does not implement a trusted login/state transaction.
/// </summary>
public sealed class WindowsTpmCashierStateSource : IDisposable
{
    private readonly object gate = new();
    private readonly WindowsTpmProtectedStateStore checkpoint;
    private readonly IProtectedCashierSnapshotStorage storage;
    private readonly ProtectedCashierWorkplaceScope expectedScope;
    private readonly ProtectedCashierDeviceBinding expectedDevice;
    private readonly byte[] expectedCheckpointContext;
    private bool unavailable;

    internal WindowsTpmCashierStateSource(
        WindowsTpmProtectedStateStore checkpoint, IProtectedCashierSnapshotStorage storage,
        ProtectedCashierWorkplaceScope expectedScope, ProtectedCashierDeviceBinding expectedDevice,
        ReadOnlySpan<byte> expectedCheckpointContext)
    {
        if (checkpoint is null || storage is null || expectedScope is null || expectedDevice is null ||
            expectedCheckpointContext.Length != 32) { throw ProtectedCashierSnapshotParser.Failure(); }
        this.checkpoint = checkpoint;
        this.storage = storage;
        this.expectedScope = expectedScope;
        this.expectedDevice = expectedDevice;
        this.expectedCheckpointContext = expectedCheckpointContext.ToArray();
    }

    public static WindowsTpmCashierStateSource OpenExisting(
        string checkpointPath, string snapshotPath, TpmNvCounterBinding binding,
        ReadOnlySpan<byte> authorization, ProtectedCashierWorkplaceScope expectedScope,
        ProtectedCashierDeviceBinding expectedDevice)
    {
        if (!OperatingSystem.IsWindows())
        {
            throw new PlatformNotSupportedException("TPM cashier state requires Windows.");
        }
        WindowsTpmProtectedStateStore? opened = null;
        WindowsTpmCashierStateSource? candidate = null;
        try
        {
            if (binding is null || expectedScope is null || expectedDevice is null ||
                string.Equals(Path.GetFullPath(checkpointPath), Path.GetFullPath(snapshotPath),
                    StringComparison.OrdinalIgnoreCase)) { throw ProtectedCashierSnapshotParser.Failure(); }
            var files = new ExistingProtectedCashierSnapshotStorage(snapshotPath);
            opened = WindowsTpmProtectedStateStore.OpenExisting(checkpointPath, binding, authorization);
            candidate = new WindowsTpmCashierStateSource(opened, files, expectedScope, expectedDevice,
                binding.ContextSha256);
            opened = null;
            candidate.ReadSnapshot();
            var result = candidate;
            candidate = null;
            return result;
        }
        catch (Exception) { throw ProtectedCashierSnapshotParser.Failure(); }
        finally
        {
            candidate?.Dispose();
            opened?.Dispose();
        }
    }

    public CheckpointVerifiedCashierSnapshot ReadSnapshot()
    {
        lock (gate)
        {
            RequireAvailable();
            try
            {
                using var file = storage.OpenExclusive();
                var raw = file.ReadBounded(ProtectedCashierStateSnapshot.MaximumPayloadBytes);
                try
                {
                    var snapshot = ProtectedCashierStateSnapshot.Parse(raw);
                    snapshot.AssertBinding(expectedScope, expectedDevice, expectedCheckpointContext);
                    var before = checkpoint.ReadExisting();
                    if (!CryptographicOperations.FixedTimeEquals(before.StateDigest, snapshot.PayloadDigest))
                    {
                        throw ProtectedCashierSnapshotParser.Failure();
                    }
                    var after = checkpoint.ReadExisting();
                    if (after.Counter != before.Counter ||
                        !CryptographicOperations.FixedTimeEquals(after.StateDigest, before.StateDigest))
                    {
                        throw ProtectedCashierSnapshotParser.Failure();
                    }
                    return new CheckpointVerifiedCashierSnapshot(snapshot, after.Counter);
                    // File close runs before a result leaves this try; close failures invalidate the source.
                }
                finally { CryptographicOperations.ZeroMemory(raw); }
            }
            catch (Exception)
            {
                Invalidate();
                throw ProtectedCashierSnapshotParser.Failure();
            }
        }
    }

    /// <summary>
    /// Selects current sealed metadata only. Tenant policy can restrict these candidates;
    /// stored observed time cannot extend a lease or replace a fresh trusted-clock check.
    /// Expected business denials do not invalidate an otherwise intact source.
    /// </summary>
    public CheckpointVerifiedCashierState ReadCashierState(Guid userId)
    {
        lock (gate)
        {
            RequireAvailable();
            if (!ProtectedCashierSnapshotParser.IsUuid4(userId)) { throw ProtectedCashierSnapshotParser.Failure(); }
            var verified = ReadSnapshot();
            var snapshot = verified.Snapshot;
            var cashier = snapshot.Cashiers.SingleOrDefault(row => row.Authorization.Scope.UserId == userId);
            var policy = snapshot.PolicyFloor.Policy;
            if (cashier is null || cashier.Authorization.Status != "active" ||
                cashier.Authorization.TenantAccessRevision != policy.Version ||
                policy.TenantStatus is not ("active" or "trial" or "grace_period"))
            {
                throw ProtectedCashierSnapshotParser.Failure();
            }
            var restrictionsApply = policy.ExpiresAt is null || snapshot.ObservedTime.UtcNow < policy.ExpiresAt.Value;
            if (restrictionsApply &&
                (((policy.Mode == "readonly" || policy.BlockedModules.Contains("pos")) &&
                   cashier.Authorization.AllowedCommands.Any(IsPosMutation)) ||
                 (policy.BlockedModules.Contains("inventory") &&
                   cashier.Authorization.AllowedCommands.Contains("sale.cash.complete"))))
            {
                throw ProtectedCashierSnapshotParser.Failure();
            }
            return new CheckpointVerifiedCashierState(verified, cashier);
        }
    }

    private static bool IsPosMutation(string command)
        => command is "sale.cash.complete" or "shift.open" or "shift.close";

    private void RequireAvailable()
    {
        if (unavailable) { throw ProtectedCashierSnapshotParser.Failure(); }
    }

    private void Invalidate()
    {
        if (unavailable) { return; }
        unavailable = true;
        try { checkpoint.Dispose(); }
        catch (Exception) { /* Disposal cannot make admission data available again. */ }
    }

    public void Dispose()
    {
        lock (gate) { Invalidate(); }
    }

    public override string ToString() => nameof(WindowsTpmCashierStateSource);
}

internal interface IProtectedCashierSnapshotStorage
{
    IProtectedCashierSnapshotFile OpenExclusive();
}

internal interface IProtectedCashierSnapshotFile : IDisposable
{
    byte[] ReadBounded(int maximumBytes);
}

// Independent enrollment/service ownership is a precondition. Reparse checks alone are not ACLs.
internal sealed class ExistingProtectedCashierSnapshotStorage : IProtectedCashierSnapshotStorage
{
    private readonly string path;

    internal ExistingProtectedCashierSnapshotStorage(string path)
    {
        if (string.IsNullOrWhiteSpace(path) || !Path.IsPathFullyQualified(path) || path.StartsWith(@"\\"))
        {
            throw ProtectedCashierSnapshotParser.Failure();
        }
        this.path = Path.GetFullPath(path);
        if (OperatingSystem.IsWindows() && this.path.AsSpan(3).Contains(':')) { throw ProtectedCashierSnapshotParser.Failure(); }
        AssertExistingRegularPath();
    }

    public IProtectedCashierSnapshotFile OpenExclusive()
    {
        AssertExistingRegularPath();
        var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.None,
            4096, FileOptions.SequentialScan);
        try
        {
            AssertExistingRegularPath();
            return new ExistingSnapshotFile(stream);
        }
        catch
        {
            stream.Dispose();
            throw;
        }
    }

    private void AssertExistingRegularPath()
    {
        if ((File.GetAttributes(path) & (FileAttributes.Directory | FileAttributes.ReparsePoint | FileAttributes.Device)) != 0)
        {
            throw ProtectedCashierSnapshotParser.Failure();
        }
        for (var directory = Path.GetDirectoryName(path); directory is not null;
             directory = Path.GetDirectoryName(directory))
        {
            if ((File.GetAttributes(directory) & FileAttributes.ReparsePoint) != 0)
            {
                throw ProtectedCashierSnapshotParser.Failure();
            }
        }
    }

    private sealed class ExistingSnapshotFile(FileStream stream) : IProtectedCashierSnapshotFile
    {
        public byte[] ReadBounded(int maximumBytes)
        {
            var length = stream.Length;
            if (maximumBytes != ProtectedCashierStateSnapshot.MaximumPayloadBytes || length <= 0 || length > maximumBytes)
            {
                throw ProtectedCashierSnapshotParser.Failure();
            }
            var bytes = new byte[(int)length];
            try
            {
                stream.Position = 0;
                stream.ReadExactly(bytes);
                if (stream.Length != length) { throw ProtectedCashierSnapshotParser.Failure(); }
                return bytes;
            }
            catch
            {
                CryptographicOperations.ZeroMemory(bytes);
                throw;
            }
        }
        public void Dispose() => stream.Dispose();
    }
}
