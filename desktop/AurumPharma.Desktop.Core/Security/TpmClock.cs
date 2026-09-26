using System.Buffers.Binary;
using System.Security.Cryptography;

namespace AurumPharma.Desktop.Core.Security;

/// <summary>
/// Raw, unsigned TPM 2.0 time information. This is neither UTC nor authenticated
/// attestation, and must never itself authorize login or extend a lease.
/// </summary>
public sealed class TpmClockReading
{
    internal TpmClockReading(
        ulong timeMilliseconds,
        ulong clockMilliseconds,
        uint resetCount,
        uint restartCount,
        bool safe)
    {
        TimeMilliseconds = timeMilliseconds;
        ClockMilliseconds = clockMilliseconds;
        ResetCount = resetCount;
        RestartCount = restartCount;
        Safe = safe;
    }

    public ulong TimeMilliseconds { get; }
    public ulong ClockMilliseconds { get; }
    public uint ResetCount { get; }
    public uint RestartCount { get; }
    public bool Safe { get; }

    public override string ToString() => nameof(TpmClockReading);
}

internal static class TpmClockCodec
{
    internal const int ResponseLength = 35;

    // TPM_ST_NO_SESSIONS, ten-byte header, TPM_CC_ReadClock. No handles or
    // authorization area: callers cannot substitute a state-changing command.
    internal static byte[] CreateReadClockCommand() =>
        [0x80, 0x01, 0x00, 0x00, 0x00, 0x0A, 0x00, 0x00, 0x01, 0x81];

    internal static TpmClockReading Parse(ReadOnlySpan<byte> response)
    {
        if (response.Length != ResponseLength ||
            BinaryPrimitives.ReadUInt16BigEndian(response) != 0x8001 ||
            BinaryPrimitives.ReadUInt32BigEndian(response[2..]) != ResponseLength ||
            BinaryPrimitives.ReadUInt32BigEndian(response[6..]) != 0 ||
            response[34] > 1)
        {
            throw Failure();
        }

        return new TpmClockReading(
            BinaryPrimitives.ReadUInt64BigEndian(response[10..]),
            BinaryPrimitives.ReadUInt64BigEndian(response[18..]),
            BinaryPrimitives.ReadUInt32BigEndian(response[26..]),
            BinaryPrimitives.ReadUInt32BigEndian(response[30..]),
            response[34] == 1);
    }

    internal static CryptographicException Failure() =>
        new("Offline TPM clock is unavailable");
}
