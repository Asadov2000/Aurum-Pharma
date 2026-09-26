using System.Collections.Concurrent;
using System.Runtime.InteropServices;
using System.Runtime.Versioning;
using System.Security.Cryptography;

namespace AurumPharma.Desktop.Core.Security;

// A single process-rooted thunk and never-reused opaque IDs avoid dereferencing
// freed GCHandles or collecting delegates while a late native callback runs.
internal sealed class WindowsPowerContinuityRegistration : IDisposable
{
    private static readonly ConcurrentDictionary<nint, Action> Routes = new();
    private static readonly PowerNotificationCallback Callback = OnNotification;
    private static long nextContext;
    private readonly IPowerNotificationTransport transport;
    private readonly nint context;
    private nint registration;
    private int disposed;

    private WindowsPowerContinuityRegistration(IPowerNotificationTransport transport, nint context)
    {
        this.transport = transport;
        this.context = context;
    }

    internal static WindowsPowerContinuityRegistration Open(
        IPowerNotificationTransport transport, Action invalidate)
    {
        if (transport is null || invalidate is null)
        {
            throw Failure();
        }

        var result = new WindowsPowerContinuityRegistration(transport, AllocateContext(ref nextContext));
        if (!Routes.TryAdd(result.context, invalidate))
        {
            throw Failure();
        }

        try
        {
            if (transport.Register(Callback, result.context, out result.registration) != 0 ||
                result.registration == 0)
            {
                throw Failure();
            }

            return result;
        }
        catch (Exception)
        {
            try { result.Dispose(); }
            catch (Exception) { /* Cleanup cannot make a failed registration usable. */ }
            throw Failure();
        }
    }

    public void Dispose()
    {
        if (Interlocked.Exchange(ref disposed, 1) != 0)
        {
            return;
        }

        // Do not assume unregister drains pending callbacks. Removing this route
        // first makes late notifications harmless even when unregister fails.
        Routes.TryRemove(context, out _);
        try
        {
            if (registration != 0 && transport.Unregister(registration) != 0)
            {
                throw Failure();
            }
        }
        catch (Exception)
        {
            throw Failure();
        }
    }

    private static uint OnNotification(nint context, uint type, nint setting)
    {
        try
        {
            // No pointer dereference, IO, TPM command or session lock in callback.
            // Conservatively invalidate for every notification, including unknown types.
            if (Routes.TryGetValue(context, out var invalidate))
            {
                invalidate();
            }
            return 0;
        }
        catch (Exception)
        {
            return 31; // ERROR_GEN_FAILURE; managed exceptions never cross the ABI.
        }
    }

    internal static CryptographicException Failure() =>
        new("Offline TPM observation is unavailable");

    internal static nint AllocateContext(ref long counter)
    {
        while (true)
        {
            var previous = Volatile.Read(ref counter);
            if (previous < 0 || previous >= nint.MaxValue)
            {
                throw Failure();
            }
            var next = previous + 1;
            if (Interlocked.CompareExchange(ref counter, next, previous) == previous)
            {
                return (nint)next;
            }
        }
    }
}

[UnmanagedFunctionPointer(CallingConvention.Winapi)]
internal delegate uint PowerNotificationCallback(nint context, uint type, nint setting);

internal interface IPowerNotificationTransport
{
    uint Register(PowerNotificationCallback callback, nint context, out nint registration);
    uint Unregister(nint registration);
}

[SupportedOSPlatform("windows")]
internal sealed class NativePowerNotificationTransport : IPowerNotificationTransport
{
    public uint Register(PowerNotificationCallback callback, nint context, out nint registration)
    {
        var parameters = new SubscribeParameters
        {
            Callback = Marshal.GetFunctionPointerForDelegate(callback),
            Context = context
        };
        return PowerRegisterSuspendResumeNotification(2, in parameters, out registration);
    }

    public uint Unregister(nint registration) => PowerUnregisterSuspendResumeNotification(registration);

    [StructLayout(LayoutKind.Sequential)]
    private struct SubscribeParameters
    {
        public nint Callback;
        public nint Context;
    }

    [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
    [DllImport("powrprof.dll", ExactSpelling = true, CallingConvention = CallingConvention.Winapi)]
    private static extern uint PowerRegisterSuspendResumeNotification(
        uint flags, in SubscribeParameters recipient, out nint registration);

    [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
    [DllImport("powrprof.dll", ExactSpelling = true, CallingConvention = CallingConvention.Winapi)]
    private static extern uint PowerUnregisterSuspendResumeNotification(nint registration);
}
