using System.Security.Cryptography;

namespace AurumPharma.Desktop.Core.Security;

/// <summary>The callback must not retain or log credential material.</summary>
public delegate TResult ProtectedCredentialMaterialReader<TResult>(ReadOnlySpan<byte> pepper, string verifier);

/// <summary>
/// Point-in-time checkpoint-verified material, not proof of a password or permission to log in.
/// The owner must dispose this object and separately enforce fresh time, attempts and SQL serialization.
/// </summary>
public sealed class CheckpointVerifiedCredentialMaterial : IDisposable
{
    private readonly object gate = new();
    private readonly byte[] pepper;
    private readonly string verifier;
    private readonly byte[] bundleDigest;
    private bool disposed;

    internal CheckpointVerifiedCredentialMaterial(ProtectedCashierStateSnapshot metadata,
        ProtectedCashierRecord cashier, ulong counter, byte[] bundleDigest, string verifier, byte[] pepper)
    {
        AdmissionMetadata = metadata;
        Cashier = cashier;
        Counter = counter;
        this.bundleDigest = bundleDigest.ToArray();
        this.verifier = verifier;
        // Ownership of this exact allocation transfers from the source only after all handles close.
        this.pepper = pepper;
    }

    public ProtectedCashierStateSnapshot AdmissionMetadata { get; }
    public ProtectedCashierRecord Cashier { get; }
    public ulong Counter { get; }
    public byte[] BundleDigest => bundleDigest.ToArray();

    public TResult Use<TResult>(ProtectedCredentialMaterialReader<TResult> reader)
    {
        ArgumentNullException.ThrowIfNull(reader);
        lock (gate)
        {
            if (disposed) { throw Failure(); }
            return reader(pepper, verifier);
        }
    }

    public void Dispose()
    {
        lock (gate)
        {
            disposed = true;
            CryptographicOperations.ZeroMemory(pepper);
        }
    }

    internal static CryptographicException Failure() => new("Protected local credential material is unavailable");
    public override string ToString() => nameof(CheckpointVerifiedCredentialMaterial);
}

/// <summary>
/// Opens existing service-owned files and a provisioned TPM key only. Authenticated enrollment,
/// exclusive NV ownership and service/directory ACLs must already be independently established.
/// No creation, fallback, password checking, trusted UTC, state mutation or login admission.
/// </summary>
public sealed class WindowsTpmCredentialMaterialSource : IDisposable
{
    private readonly object gate = new();
    private readonly WindowsTpmProtectedStateStore checkpoint;
    private readonly IProtectedCashierSnapshotStorage storage;
    private readonly ProtectedCashierWorkplaceScope expectedScope;
    private readonly ProtectedCashierDeviceBinding expectedDevice;
    private readonly byte[] expectedCheckpointContext;
    private readonly Func<ProtectedCashierCredentialContext, ILocalCredentialSecretUnprotector> openProtector;
    private bool unavailable;

    internal WindowsTpmCredentialMaterialSource(WindowsTpmProtectedStateStore checkpoint,
        IProtectedCashierSnapshotStorage storage, ProtectedCashierWorkplaceScope expectedScope,
        ProtectedCashierDeviceBinding expectedDevice, ReadOnlySpan<byte> expectedCheckpointContext,
        Func<ProtectedCashierCredentialContext, ILocalCredentialSecretUnprotector> openProtector)
    {
        if (checkpoint is null || storage is null || expectedScope is null || expectedDevice is null ||
            expectedCheckpointContext.Length != 32 || openProtector is null) { throw Failure(); }
        this.checkpoint = checkpoint;
        this.storage = storage;
        this.expectedScope = expectedScope;
        this.expectedDevice = expectedDevice;
        this.expectedCheckpointContext = expectedCheckpointContext.ToArray();
        this.openProtector = openProtector;
    }

    public static WindowsTpmCredentialMaterialSource OpenExisting(string checkpointPath, string bundlePath,
        TpmNvCounterBinding binding, ReadOnlySpan<byte> authorization,
        ProtectedCashierWorkplaceScope expectedScope, ProtectedCashierDeviceBinding expectedDevice)
    {
        if (!OperatingSystem.IsWindows()) { throw new PlatformNotSupportedException("TPM credentials require Windows."); }
        WindowsTpmProtectedStateStore? opened = null;
        try
        {
            if (binding is null || string.Equals(Path.GetFullPath(checkpointPath), Path.GetFullPath(bundlePath),
                    StringComparison.OrdinalIgnoreCase)) { throw Failure(); }
            var files = new ExistingProtectedCashierSnapshotStorage(bundlePath);
            opened = WindowsTpmProtectedStateStore.OpenExisting(checkpointPath, binding, authorization);
            var result = new WindowsTpmCredentialMaterialSource(opened, files, expectedScope, expectedDevice,
                binding.ContextSha256, context => new NativeCredentialSecretUnprotector(context));
            opened = null;
            return result;
        }
        catch (Exception) { throw Failure(); }
        finally { opened?.Dispose(); }
    }

    public CheckpointVerifiedCredentialMaterial ReadMaterial(Guid userId)
    {
        lock (gate)
        {
            RequireAvailable();
            byte[]? pepper = null;
            try
            {
                if (!ProtectedCashierSnapshotParser.IsUuid4(userId)) { throw Failure(); }
                ProtectedCredentialBundle bundle;
                ProtectedCashierRecord cashier;
                CredentialBundleMaterial material;
                ulong generation;
                using (var file = storage.OpenExclusive())
                {
                    var raw = file.ReadBounded(ProtectedCredentialBundle.MaximumPayloadBytes);
                    try { bundle = ProtectedCredentialBundle.Parse(raw); }
                    finally { CryptographicOperations.ZeroMemory(raw); }
                    bundle.Snapshot.AssertBinding(expectedScope, expectedDevice, expectedCheckpointContext);
                    var before = checkpoint.ReadExisting();
                    if (!CryptographicOperations.FixedTimeEquals(before.StateDigest, bundle.PayloadDigest)) { throw Failure(); }
                    cashier = SelectCurrentCashier(bundle.Snapshot, userId);
                    material = bundle.ReadMaterial(userId);
                    // All expected decrypt parameters come from the metadata authenticated by the outer digest.
                    var contextDigest = cashier.CredentialContext.ContextDigestSha256;
                    try
                    {
                        using var protector = openProtector(cashier.CredentialContext);
                        pepper = protector.Unprotect(material.Envelope, contextDigest);
                        if (pepper is null || pepper.Length != 32) { throw Failure(); }
                    }
                    finally { CryptographicOperations.ZeroMemory(contextDigest); }
                    var after = checkpoint.ReadExisting();
                    if (after.Counter != before.Counter ||
                        !CryptographicOperations.FixedTimeEquals(after.StateDigest, before.StateDigest)) { throw Failure(); }
                    generation = after.Counter;
                }
                // Both the key and exclusive file are closed before any plaintext becomes observable.
                RequireAvailable();
                var result = new CheckpointVerifiedCredentialMaterial(bundle.Snapshot, cashier,
                    generation, bundle.PayloadDigest, material.Verifier, pepper);
                pepper = null;
                return result;
            }
            catch (Exception)
            {
                Invalidate();
                throw Failure();
            }
            finally { if (pepper is not null) { CryptographicOperations.ZeroMemory(pepper); } }
        }
    }

    private static ProtectedCashierRecord SelectCurrentCashier(ProtectedCashierStateSnapshot snapshot, Guid userId)
    {
        var cashier = snapshot.Cashiers.SingleOrDefault(row => row.Authorization.Scope.UserId == userId);
        var policy = snapshot.PolicyFloor.Policy;
        if (cashier is null || cashier.Authorization.Status != "active" ||
            cashier.Authorization.TenantAccessRevision != policy.Version ||
            policy.TenantStatus is not ("active" or "trial" or "grace_period")) { throw Failure(); }
        // This conservative metadata selection is not a fresh-time policy evaluation or a login decision.
        var restrictionsApply = policy.ExpiresAt is null || snapshot.ObservedTime.UtcNow < policy.ExpiresAt.Value;
        if (restrictionsApply &&
            (((policy.Mode == "readonly" || policy.BlockedModules.Contains("pos")) &&
              cashier.Authorization.AllowedCommands.Any(command => command is "sale.cash.complete" or "shift.open" or "shift.close")) ||
             (policy.BlockedModules.Contains("inventory") && cashier.Authorization.AllowedCommands.Contains("sale.cash.complete"))))
        { throw Failure(); }
        return cashier;
    }

    private void RequireAvailable() { if (unavailable) { throw Failure(); } }
    private void Invalidate()
    {
        unavailable = true;
        try { checkpoint.Dispose(); }
        catch (Exception) { /* A close failure cannot restore access to material. */ }
    }
    private static CryptographicException Failure() => CheckpointVerifiedCredentialMaterial.Failure();
    public void Dispose() { lock (gate) { Invalidate(); } }
    public override string ToString() => nameof(WindowsTpmCredentialMaterialSource);
}

internal interface ILocalCredentialSecretUnprotector : IDisposable
{
    byte[] Unprotect(ProtectedLocalSecretEnvelope envelope, ReadOnlySpan<byte> contextSha256);
}

internal sealed class NativeCredentialSecretUnprotector : ILocalCredentialSecretUnprotector
{
    private readonly WindowsTpmSecretProtector protector;
    internal NativeCredentialSecretUnprotector(ProtectedCashierCredentialContext context)
        => protector = WindowsTpmSecretProtector.OpenExisting(context.ProtectionKey.KeyId,
            Convert.FromHexString(context.ProtectionKey.SpkiSha256));
    public byte[] Unprotect(ProtectedLocalSecretEnvelope envelope, ReadOnlySpan<byte> contextSha256)
        => protector.Unprotect(envelope, contextSha256);
    public void Dispose() => protector.Dispose();
}
