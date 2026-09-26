using System.Runtime.InteropServices;
using System.Runtime.Versioning;

namespace AurumPharma.Desktop.Core.Security;

/// <summary>
/// Reads raw TPM 2.0 clock data through Windows TPM Base Services.
/// The reading is unsigned and is not trusted UTC, caller authorization or rollback protection.
/// </summary>
public static class WindowsTpmClockReader
{
    private const int ResponseCapacity = TpmClockCodec.ResponseLength;

    /// <summary>Submits only the fixed, read-only TPM2_ReadClock command.</summary>
    public static TpmClockReading Read()
    {
        if (!OperatingSystem.IsWindows())
        {
            throw new PlatformNotSupportedException("TPM clock reading requires Windows.");
        }

        return Read(new NativeTpmClockTransport());
    }

    internal static TpmClockReading Read(ITpmClockTransport transport)
    {
        nint context = 0;
        var succeeded = false;
        TpmClockReading? reading = null;
        try
        {
            if (transport is null || transport.Create(out context) != 0 || context == 0)
            {
                throw TpmClockCodec.Failure();
            }

            var command = TpmClockCodec.CreateReadClockCommand();
            var response = new byte[ResponseCapacity];
            uint responseLength = ResponseCapacity;
            if (transport.Submit(context, command, response, ref responseLength) != 0 ||
                responseLength > ResponseCapacity)
            {
                throw TpmClockCodec.Failure();
            }

            // TBS success only means submission succeeded. The codec separately checks
            // the TPM return code and the exact response layout before exposing data.
            reading = TpmClockCodec.Parse(response.AsSpan(0, (int)responseLength));
            succeeded = true;
        }
        catch (Exception)
        {
            // Neither native status codes nor transport exception details escape.
            succeeded = false;
        }
        finally
        {
            if (context != 0 && transport is not null)
            {
                try
                {
                    if (transport.Close(context) != 0)
                    {
                        succeeded = false;
                    }
                }
                catch (Exception)
                {
                    succeeded = false;
                }
            }
        }

        if (!succeeded || reading is null)
        {
            throw TpmClockCodec.Failure();
        }

        return reading;
    }

    [SupportedOSPlatform("windows")]
    private sealed class NativeTpmClockTransport : ITpmClockTransport
    {
        public uint Create(out nint context)
        {
            // Version 2, includeTPM20 only. requestRaw and includeTPM12 remain unset.
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
            nint context,
            uint locality,
            uint priority,
            [In] byte[] command,
            uint commandLength,
            [Out] byte[] response,
            ref uint responseLength);

        [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
        [DllImport("tbs.dll", ExactSpelling = true, CallingConvention = CallingConvention.Winapi)]
        private static extern uint Tbsip_Context_Close(nint context);
    }
}

// Internal transport seam supports deterministic tests without accessing hardware.
internal interface ITpmClockTransport
{
    uint Create(out nint context);
    uint Submit(nint context, byte[] command, byte[] response, ref uint responseLength);
    uint Close(nint context);
}
