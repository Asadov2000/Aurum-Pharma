using System.Buffers.Binary;
using System.Security.Cryptography;

namespace AurumPharma.Desktop.Core.Security;

// Fixed TPM 2.0 commands only. No provisioning, arbitrary command or software fallback.
internal static class TpmNvCounterCodec
{
    internal const uint Attributes = 0x22040014; // AUTHREAD | AUTHWRITE | COUNTER | NO_DA | WRITTEN
    internal const int PublicResponseLength = 62;
    internal const int ReadResponseLength = 29;
    internal const int IncrementResponseLength = 19;
    private const ushort Sha256 = 0x000B;
    private const ushort NoSessions = 0x8001;
    private const ushort Sessions = 0x8002;
    private const uint PasswordSession = 0x40000009;

    internal static bool IsIndex(uint index) => (index & 0xFF000000) == 0x01000000;

    internal static byte[] CreateReadPublicCommand(uint index)
    {
        var command = Header(NoSessions, 14, 0x00000169);
        BinaryPrimitives.WriteUInt32BigEndian(command.AsSpan(10), index);
        return command;
    }

    internal static byte[] CreateReadCommand(uint index, ReadOnlySpan<byte> authorization)
    {
        var command = AuthorizedCommand(index, authorization, 67, 0x0000014E);
        BinaryPrimitives.WriteUInt16BigEndian(command.AsSpan(63), 8);
        // The zero-initialized final UINT16 is offset zero.
        return command;
    }

    internal static byte[] CreateIncrementCommand(uint index, ReadOnlySpan<byte> authorization)
        => AuthorizedCommand(index, authorization, 63, 0x00000134);

    internal static void ValidatePublicResponse(TpmNvCounterBinding binding, ReadOnlySpan<byte> response)
    {
        ValidateHeader(response, NoSessions, PublicResponseLength);
        if (BinaryPrimitives.ReadUInt16BigEndian(response[10..]) != 14 ||
            BinaryPrimitives.ReadUInt32BigEndian(response[12..]) != binding.Index ||
            BinaryPrimitives.ReadUInt16BigEndian(response[16..]) != Sha256 ||
            BinaryPrimitives.ReadUInt32BigEndian(response[18..]) != Attributes ||
            BinaryPrimitives.ReadUInt16BigEndian(response[22..]) != 0 ||
            BinaryPrimitives.ReadUInt16BigEndian(response[24..]) != 8 ||
            BinaryPrimitives.ReadUInt16BigEndian(response[26..]) != 34 ||
            BinaryPrimitives.ReadUInt16BigEndian(response[28..]) != Sha256 ||
            !CryptographicOperations.FixedTimeEquals(response[28..], binding.ExpectedNameSpan))
        {
            throw Failure();
        }

        Span<byte> publicHash = stackalloc byte[32];
        try
        {
            SHA256.HashData(response.Slice(12, 14), publicHash);
            if (!CryptographicOperations.FixedTimeEquals(publicHash, response[30..]))
            {
                throw Failure();
            }
        }
        finally
        {
            CryptographicOperations.ZeroMemory(publicHash);
        }
    }

    internal static ulong ReadCounterResponse(ReadOnlySpan<byte> response)
    {
        ValidateHeader(response, Sessions, ReadResponseLength);
        if (BinaryPrimitives.ReadUInt32BigEndian(response[10..]) != 10 ||
            BinaryPrimitives.ReadUInt16BigEndian(response[14..]) != 8)
        {
            throw Failure();
        }

        ValidatePasswordResponse(response[24..]);
        return BinaryPrimitives.ReadUInt64BigEndian(response[16..]);
    }

    internal static void ValidateIncrementResponse(ReadOnlySpan<byte> response)
    {
        ValidateHeader(response, Sessions, IncrementResponseLength);
        if (BinaryPrimitives.ReadUInt32BigEndian(response[10..]) != 0)
        {
            throw Failure();
        }

        ValidatePasswordResponse(response[14..]);
    }

    private static byte[] AuthorizedCommand(
        uint index, ReadOnlySpan<byte> authorization, int length, uint code)
    {
        if (!IsIndex(index) || authorization.Length != 32)
        {
            throw Failure();
        }

        var command = Header(Sessions, length, code);
        BinaryPrimitives.WriteUInt32BigEndian(command.AsSpan(10), index); // authHandle
        BinaryPrimitives.WriteUInt32BigEndian(command.AsSpan(14), index); // nvIndex
        BinaryPrimitives.WriteUInt32BigEndian(command.AsSpan(18), 41); // authorizationSize
        BinaryPrimitives.WriteUInt32BigEndian(command.AsSpan(22), PasswordSession);
        // Empty nonce and zero command sessionAttributes are already initialized.
        BinaryPrimitives.WriteUInt16BigEndian(command.AsSpan(29), 32);
        authorization.CopyTo(command.AsSpan(31));
        return command;
    }

    private static byte[] Header(ushort tag, int length, uint code)
    {
        var command = new byte[length];
        BinaryPrimitives.WriteUInt16BigEndian(command, tag);
        BinaryPrimitives.WriteUInt32BigEndian(command.AsSpan(2), (uint)length);
        BinaryPrimitives.WriteUInt32BigEndian(command.AsSpan(6), code);
        return command;
    }

    private static void ValidateHeader(ReadOnlySpan<byte> response, ushort tag, int length)
    {
        if (response.Length != length ||
            BinaryPrimitives.ReadUInt16BigEndian(response) != tag ||
            BinaryPrimitives.ReadUInt32BigEndian(response[2..]) != length ||
            BinaryPrimitives.ReadUInt32BigEndian(response[6..]) != 0)
        {
            throw Failure();
        }
    }

    private static void ValidatePasswordResponse(ReadOnlySpan<byte> response)
    {
        // TPM_RS_PW response has empty nonce/HMAC and continueSession SET.
        // Microsoft TPM reference SessionProcess.c, BuildResponseSession().
        if (!response.SequenceEqual(new byte[] { 0, 0, 1, 0, 0 }))
        {
            throw Failure();
        }
    }

    internal static CryptographicException Failure()
        => new("Protected local state counter is unavailable.");
}
