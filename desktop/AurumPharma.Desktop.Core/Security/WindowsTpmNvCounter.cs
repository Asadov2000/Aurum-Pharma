using System.Buffers.Binary;
using System.Runtime.InteropServices;
using System.Runtime.Versioning;
using System.Security.Cryptography;

namespace AurumPharma.Desktop.Core.Security;

/// <summary>
/// Independently trusted enrollment binding, never reconstructed from SQL, a journal or UI input.
/// Provisioning must pin the index Name, auth digest, device/context and minimum counter together.
/// This value does not itself authenticate enrollment or provision a TPM.
/// </summary>
public sealed class TpmNvCounterBinding
{
    private readonly byte[] expectedName;
    private readonly byte[] expectedAuthSha256;
    private readonly byte[] contextSha256;

    public TpmNvCounterBinding(
        uint index,
        ReadOnlySpan<byte> expectedName,
        ReadOnlySpan<byte> expectedAuthSha256,
        ReadOnlySpan<byte> contextSha256,
        ulong minimumCounter)
    {
        if (!TpmNvCounterCodec.IsIndex(index) || expectedName.Length != 34 ||
            BinaryPrimitives.ReadUInt16BigEndian(expectedName) != 0x000B ||
            expectedAuthSha256.Length != 32 || contextSha256.Length != 32 || minimumCounter == 0)
        {
            throw TpmNvCounterCodec.Failure();
        }

        Index = index;
        MinimumCounter = minimumCounter;
        this.expectedName = expectedName.ToArray();
        this.expectedAuthSha256 = expectedAuthSha256.ToArray();
        this.contextSha256 = contextSha256.ToArray();
    }

    public uint Index { get; }
    public ulong MinimumCounter { get; }
    public byte[] ExpectedName => expectedName.ToArray();
    public byte[] ExpectedAuthSha256 => expectedAuthSha256.ToArray();
    public byte[] ContextSha256 => contextSha256.ToArray();
    internal ReadOnlySpan<byte> ExpectedNameSpan => expectedName;
    internal ReadOnlySpan<byte> ExpectedAuthSha256Span => expectedAuthSha256;

    public override string ToString() => nameof(TpmNvCounterBinding);
}

/// <summary>
/// Opens an existing, initialized TPM 2.0 NV counter with a narrow authenticated profile.
/// No DefineSpace, UndefineSpace, Clear, recovery, clock or login-admission operation is exposed.
/// The trusted owner supplies auth from the existing TPM protector and a signed enrollment binding.
/// The password authorization crosses only the local TBS boundary; this is not remote attestation.
/// </summary>
public sealed class WindowsTpmNvCounter : IProtectedStateCounter
{
    private readonly object gate = new();
    private readonly TpmNvCounterBinding binding;
    private readonly ITpmNvCounterTransport transport;
    private readonly byte[] authorization;
    private ulong current;
    private bool invalidated;
    private bool disposed;

    private WindowsTpmNvCounter(
        TpmNvCounterBinding binding, ReadOnlySpan<byte> authorization, ITpmNvCounterTransport transport)
    {
        this.binding = binding;
        this.transport = transport;
        this.authorization = authorization.ToArray();
    }

    public static WindowsTpmNvCounter OpenExisting(
        TpmNvCounterBinding binding, ReadOnlySpan<byte> authorization)
    {
        if (!OperatingSystem.IsWindows())
        {
            throw new PlatformNotSupportedException("TPM protected state requires Windows.");
        }

        return OpenExisting(binding, authorization, new NativeTpmNvCounterTransport());
    }

    // Deterministic transport seam; never selected by the production factory.
    internal static WindowsTpmNvCounter OpenExisting(
        TpmNvCounterBinding binding, ReadOnlySpan<byte> authorization, ITpmNvCounterTransport transport)
    {
        if (binding is null || transport is null || authorization.Length != 32)
        {
            throw TpmNvCounterCodec.Failure();
        }

        Span<byte> authHash = stackalloc byte[32];
        try
        {
            SHA256.HashData(authorization, authHash);
            if (!CryptographicOperations.FixedTimeEquals(authHash, binding.ExpectedAuthSha256Span))
            {
                throw TpmNvCounterCodec.Failure();
            }
        }
        finally
        {
            CryptographicOperations.ZeroMemory(authHash);
        }

        var result = new WindowsTpmNvCounter(binding, authorization, transport);
        result.current = result.WithContext(context =>
        {
            var observed = result.ReadValidatedCounter(context);
            if (observed < binding.MinimumCounter)
            {
                throw TpmNvCounterCodec.Failure();
            }
            return observed;
        });
        return result;
    }

    public ulong ReadCurrent()
    {
        lock (gate)
        {
            RequireUsable();
            return WithContext(context =>
            {
                var observed = ReadValidatedCounter(context);
                if (observed != current)
                {
                    throw TpmNvCounterCodec.Failure();
                }
                return observed;
            });
        }
    }

    public ulong Advance(ulong expected)
    {
        lock (gate)
        {
            RequireUsable();
            if (expected != current || expected == ulong.MaxValue)
            {
                Invalidate();
                throw TpmNvCounterCodec.Failure();
            }

            var next = WithContext(context =>
            {
                if (ReadValidatedCounter(context) != expected)
                {
                    throw TpmNvCounterCodec.Failure();
                }

                var response = Exchange(context,
                    TpmNvCounterCodec.CreateIncrementCommand(binding.Index, authorization),
                    TpmNvCounterCodec.IncrementResponseLength);
                try { TpmNvCounterCodec.ValidateIncrementResponse(response); }
                finally { CryptographicOperations.ZeroMemory(response); }

                var observed = ReadValidatedCounter(context);
                if (observed != expected + 1)
                {
                    throw TpmNvCounterCodec.Failure();
                }
                return observed;
            });
            current = next;
            return next;
        }
    }

    private ulong ReadValidatedCounter(nint context)
    {
        var publicResponse = Exchange(context, TpmNvCounterCodec.CreateReadPublicCommand(binding.Index),
            TpmNvCounterCodec.PublicResponseLength);
        try { TpmNvCounterCodec.ValidatePublicResponse(binding, publicResponse); }
        finally { CryptographicOperations.ZeroMemory(publicResponse); }

        var response = Exchange(context, TpmNvCounterCodec.CreateReadCommand(binding.Index, authorization),
            TpmNvCounterCodec.ReadResponseLength);
        try { return TpmNvCounterCodec.ReadCounterResponse(response); }
        finally { CryptographicOperations.ZeroMemory(response); }
    }

    private byte[] Exchange(nint context, byte[] command, int capacity)
    {
        var response = new byte[capacity];
        uint length = (uint)capacity;
        try
        {
            if (transport.Submit(context, command, response, ref length) != 0 || length != capacity)
            {
                throw TpmNvCounterCodec.Failure();
            }
            return response;
        }
        catch
        {
            CryptographicOperations.ZeroMemory(response);
            throw;
        }
        finally
        {
            // Includes the password auth area, even on native/transport exceptions.
            CryptographicOperations.ZeroMemory(command);
        }
    }

    private ulong WithContext(Func<nint, ulong> operation)
    {
        nint context = 0;
        ulong observed = 0;
        var succeeded = false;
        try
        {
            if (transport.Create(out context) != 0 || context == 0)
            {
                throw TpmNvCounterCodec.Failure();
            }
            observed = operation(context);
            succeeded = true;
        }
        catch (Exception)
        {
            succeeded = false;
        }
        finally
        {
            if (context != 0)
            {
                try
                {
                    if (transport.Close(context) != 0) { succeeded = false; }
                }
                catch (Exception) { succeeded = false; }
            }
        }

        if (!succeeded)
        {
            Invalidate();
            throw TpmNvCounterCodec.Failure();
        }
        return observed;
    }

    private void RequireUsable()
    {
        if (disposed || invalidated) { throw TpmNvCounterCodec.Failure(); }
    }

    private void Invalidate()
    {
        invalidated = true;
        CryptographicOperations.ZeroMemory(authorization);
    }

    public void Dispose()
    {
        lock (gate)
        {
            if (disposed) { return; }
            disposed = true;
            Invalidate();
        }
    }

    public override string ToString() => nameof(WindowsTpmNvCounter);

    [SupportedOSPlatform("windows")]
    private sealed class NativeTpmNvCounterTransport : ITpmNvCounterTransport
    {
        public uint Create(out nint context)
        {
            var parameters = new ContextParameters { Version = 2, Flags = 4 };
            return Tbsi_Context_Create(in parameters, out context);
        }

        public uint Submit(nint context, byte[] command, byte[] response, ref uint responseLength)
            => Tbsip_Submit_Command(context, 0, 200, command, (uint)command.Length, response, ref responseLength);

        public uint Close(nint context) => Tbsip_Context_Close(context);

        [StructLayout(LayoutKind.Sequential)]
        private struct ContextParameters
        {
            public uint Version;
            public uint Flags;
        }

        [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
        [DllImport("tbs.dll", ExactSpelling = true, CallingConvention = CallingConvention.Winapi)]
        private static extern uint Tbsi_Context_Create(in ContextParameters parameters, out nint context);

        [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
        [DllImport("tbs.dll", ExactSpelling = true, CallingConvention = CallingConvention.Winapi)]
        private static extern uint Tbsip_Submit_Command(
            nint context, uint locality, uint priority, [In] byte[] command, uint commandLength,
            [Out] byte[] response, ref uint responseLength);

        [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
        [DllImport("tbs.dll", ExactSpelling = true, CallingConvention = CallingConvention.Winapi)]
        private static extern uint Tbsip_Context_Close(nint context);
    }
}

internal interface IProtectedStateCounter : IDisposable
{
    ulong ReadCurrent();
    ulong Advance(ulong expected);
}

internal interface ITpmNvCounterTransport
{
    uint Create(out nint context);
    uint Submit(nint context, byte[] command, byte[] response, ref uint responseLength);
    uint Close(nint context);
}
