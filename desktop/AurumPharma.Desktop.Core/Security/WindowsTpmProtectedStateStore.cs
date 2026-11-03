using System.Security.Cryptography;

namespace AurumPharma.Desktop.Core.Security;

/// <summary>An authenticated digest of the complete protected state, not an authorization decision.</summary>
public sealed class ProtectedLocalStateCheckpoint
{
    private readonly byte[] bindingSha256;
    private readonly byte[] stateDigest;

    internal ProtectedLocalStateCheckpoint(
        ReadOnlySpan<byte> bindingSha256, ulong counter, ReadOnlySpan<byte> stateDigest)
    {
        this.bindingSha256 = bindingSha256.ToArray();
        this.stateDigest = stateDigest.ToArray();
        Counter = counter;
    }

    public ulong Counter { get; }
    public byte[] StateDigest => stateDigest.ToArray();
    internal ReadOnlySpan<byte> BindingSpan => bindingSha256;
    internal ReadOnlySpan<byte> StateSpan => stateDigest;
    public override string ToString() => nameof(ProtectedLocalStateCheckpoint);
}

/// <summary>
/// Existing-index, existing-file checkpoint boundary for the future trusted local service.
/// The independently authenticated enrollment must pin the binding and the service-owned file path.
/// It must also enforce exclusive ownership of this NV index across all service processes.
/// This component does not establish enrollment, validate Windows service ACLs, authorize login,
/// provide UTC, initialize storage or repair a SQL/checkpoint forward gap.
/// </summary>
public sealed class WindowsTpmProtectedStateStore : IDisposable
{
    private readonly object gate = new();
    private readonly IProtectedStateCounter counter;
    private readonly IProtectedStateCheckpointStorage storage;
    private readonly byte[] bindingSha256;
    private readonly byte[] authenticationKey;
    private bool unavailable;

    internal WindowsTpmProtectedStateStore(
        IProtectedStateCounter counter,
        IProtectedStateCheckpointStorage storage,
        ReadOnlySpan<byte> bindingSha256,
        ReadOnlySpan<byte> authenticationKey)
    {
        if (counter is null || storage is null || bindingSha256.Length != 32 || authenticationKey.Length != 32)
        {
            throw ProtectedLocalStateCheckpointCodec.Failure();
        }
        this.counter = counter;
        this.storage = storage;
        this.bindingSha256 = bindingSha256.ToArray();
        this.authenticationKey = authenticationKey.ToArray();
    }

    /// <summary>
    /// Opens only a previously provisioned checkpoint and authenticated TPM counter.
    /// Authorization must be unwrapped using the existing TPM protector and independently pinned
    /// enrollment. No caller, SQL row, journal or encrypted envelope can establish that trust.
    /// </summary>
    public static WindowsTpmProtectedStateStore OpenExisting(
        string checkpointPath, TpmNvCounterBinding binding, ReadOnlySpan<byte> authorization)
    {
        if (!OperatingSystem.IsWindows())
        {
            throw new PlatformNotSupportedException("TPM protected state requires Windows.");
        }
        WindowsTpmNvCounter? nativeCounter = null;
        WindowsTpmProtectedStateStore? candidate = null;
        byte[]? key = null;
        try
        {
            var files = new ExistingProtectedStateCheckpointFileStorage(checkpointPath);
            key = ProtectedLocalStateCheckpointCodec.DeriveAuthenticationKey(binding, authorization);
            var identity = ProtectedLocalStateCheckpointCodec.GetBindingDigest(binding);
            nativeCounter = WindowsTpmNvCounter.OpenExisting(binding, authorization);
            candidate = new WindowsTpmProtectedStateStore(nativeCounter, files, identity, key);
            nativeCounter = null;
            candidate.ReadExisting();
            var result = candidate;
            candidate = null;
            return result;
        }
        catch (Exception)
        {
            throw ProtectedLocalStateCheckpointCodec.Failure();
        }
        finally
        {
            candidate?.Dispose();
            nativeCounter?.Dispose();
            if (key is not null) { CryptographicOperations.ZeroMemory(key); }
        }
    }

    public ProtectedLocalStateCheckpoint ReadExisting()
    {
        lock (gate)
        {
            RequireAvailable();
            try
            {
                using var file = storage.OpenExclusive();
                return ReadVerified(file);
            }
            catch (Exception)
            {
                Invalidate();
                throw ProtectedLocalStateCheckpointCodec.Failure();
            }
        }
    }

    /// <summary>
    /// Holds the file's exclusive handle through compare, hardware advance, durable write and close.
    /// A post-advance failure never rolls the TPM back or silently adopts/restores an older frame.
    /// The future provider must call this before SQL commit and reject any resulting forward gap.
    /// </summary>
    public ProtectedLocalStateCheckpoint Advance(
        ProtectedLocalStateCheckpoint expected, ReadOnlySpan<byte> nextStateDigest)
    {
        lock (gate)
        {
            RequireAvailable();
            try
            {
                if (expected is null || nextStateDigest.Length != 32)
                {
                    throw ProtectedLocalStateCheckpointCodec.Failure();
                }
                using var file = storage.OpenExclusive();
                var actual = ReadVerified(file);
                if (actual.Counter != expected.Counter || actual.Counter == ulong.MaxValue ||
                    !CryptographicOperations.FixedTimeEquals(bindingSha256, expected.BindingSpan) ||
                    !CryptographicOperations.FixedTimeEquals(actual.StateSpan, expected.StateSpan) ||
                    CryptographicOperations.FixedTimeEquals(actual.StateSpan, nextStateDigest))
                {
                    throw ProtectedLocalStateCheckpointCodec.Failure();
                }
                var nextCounter = actual.Counter + 1;
                var frame = ProtectedLocalStateCheckpointCodec.CreateFrame(
                    bindingSha256, nextCounter, nextStateDigest, authenticationKey);
                try
                {
                    if (counter.Advance(actual.Counter) != nextCounter)
                    {
                        throw ProtectedLocalStateCheckpointCodec.Failure();
                    }
                    file.WriteAndFlush(frame);
                    var stored = file.ReadExact(ProtectedLocalStateCheckpointCodec.FrameLength);
                    try
                    {
                        if (!CryptographicOperations.FixedTimeEquals(stored, frame))
                        {
                            throw ProtectedLocalStateCheckpointCodec.Failure();
                        }
                        return ProtectedLocalStateCheckpointCodec.ParseFrame(
                            stored, bindingSha256, nextCounter, authenticationKey);
                    }
                    finally { CryptographicOperations.ZeroMemory(stored); }
                }
                finally { CryptographicOperations.ZeroMemory(frame); }
                // The using declaration closes the file before a result can leave the method.
            }
            catch (Exception)
            {
                Invalidate();
                throw ProtectedLocalStateCheckpointCodec.Failure();
            }
        }
    }

    private ProtectedLocalStateCheckpoint ReadVerified(IProtectedStateCheckpointFile file)
    {
        var frame = file.ReadExact(ProtectedLocalStateCheckpointCodec.FrameLength);
        try
        {
            return ProtectedLocalStateCheckpointCodec.ParseFrame(
                frame, bindingSha256, counter.ReadCurrent(), authenticationKey);
        }
        finally { CryptographicOperations.ZeroMemory(frame); }
    }

    private void RequireAvailable()
    {
        if (unavailable) { throw ProtectedLocalStateCheckpointCodec.Failure(); }
    }

    private void Invalidate()
    {
        if (unavailable) { return; }
        unavailable = true;
        CryptographicOperations.ZeroMemory(authenticationKey);
        try { counter.Dispose(); }
        catch (Exception) { /* A failed close can never restore an available store. */ }
    }

    public void Dispose()
    {
        lock (gate) { Invalidate(); }
    }

    public override string ToString() => nameof(WindowsTpmProtectedStateStore);
}

internal interface IProtectedStateCheckpointStorage
{
    IProtectedStateCheckpointFile OpenExclusive();
}

internal interface IProtectedStateCheckpointFile : IDisposable
{
    byte[] ReadExact(int length);
    void WriteAndFlush(ReadOnlySpan<byte> frame);
}

// The future trusted service must own the directory ACL and canonical path. These checks reject
// existing reparse points; they do not replace OS ownership or claim protection from a hostile OS.
internal sealed class ExistingProtectedStateCheckpointFileStorage : IProtectedStateCheckpointStorage
{
    private readonly string path;

    internal ExistingProtectedStateCheckpointFileStorage(string path)
    {
        if (string.IsNullOrWhiteSpace(path) || !Path.IsPathFullyQualified(path) || path.StartsWith(@"\\"))
        {
            throw ProtectedLocalStateCheckpointCodec.Failure();
        }
        this.path = Path.GetFullPath(path);
        if (OperatingSystem.IsWindows() && this.path.AsSpan(3).Contains(':'))
        {
            // A pinned ordinary file cannot be substituted with an alternate data stream.
            throw ProtectedLocalStateCheckpointCodec.Failure();
        }
        AssertExistingRegularPath();
    }

    public IProtectedStateCheckpointFile OpenExclusive()
    {
        AssertExistingRegularPath();
        var stream = new FileStream(path, FileMode.Open, FileAccess.ReadWrite, FileShare.None,
            4096, FileOptions.WriteThrough);
        try
        {
            AssertExistingRegularPath();
            return new ExistingCheckpointFile(stream);
        }
        catch
        {
            stream.Dispose();
            throw;
        }
    }

    private void AssertExistingRegularPath()
    {
        var attributes = File.GetAttributes(path);
        if ((attributes & (FileAttributes.Directory | FileAttributes.ReparsePoint | FileAttributes.Device)) != 0)
        {
            throw ProtectedLocalStateCheckpointCodec.Failure();
        }
        for (var directory = Path.GetDirectoryName(path); directory is not null;
             directory = Path.GetDirectoryName(directory))
        {
            if ((File.GetAttributes(directory) & FileAttributes.ReparsePoint) != 0)
            {
                throw ProtectedLocalStateCheckpointCodec.Failure();
            }
        }
    }

    private sealed class ExistingCheckpointFile(FileStream stream) : IProtectedStateCheckpointFile
    {
        public byte[] ReadExact(int length)
        {
            if (length != ProtectedLocalStateCheckpointCodec.FrameLength || stream.Length != length)
            {
                throw ProtectedLocalStateCheckpointCodec.Failure();
            }
            var frame = new byte[length];
            try
            {
                stream.Position = 0;
                stream.ReadExactly(frame);
                return frame;
            }
            catch
            {
                CryptographicOperations.ZeroMemory(frame);
                throw;
            }
        }

        public void WriteAndFlush(ReadOnlySpan<byte> frame)
        {
            if (frame.Length != ProtectedLocalStateCheckpointCodec.FrameLength || stream.Length != frame.Length)
            {
                throw ProtectedLocalStateCheckpointCodec.Failure();
            }
            stream.Position = 0;
            stream.Write(frame);
            stream.Flush(flushToDisk: true);
        }

        public void Dispose() => stream.Dispose();
    }
}
