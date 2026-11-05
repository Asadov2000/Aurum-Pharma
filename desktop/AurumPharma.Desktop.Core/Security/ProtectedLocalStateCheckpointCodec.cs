using System.Buffers.Binary;
using System.Security.Cryptography;

namespace AurumPharma.Desktop.Core.Security;

internal static class ProtectedLocalStateCheckpointCodec
{
    private static ReadOnlySpan<byte> Magic => "AurumPharma/protected-state-checkpoint/v1\0"u8;
    private static ReadOnlySpan<byte> BindingDomain => "AurumPharma/protected-state-binding/v1\0"u8;
    private static ReadOnlySpan<byte> KeyDomain => "AurumPharma/protected-state-mac-key/v1\0"u8;
    internal static int FrameLength => Magic.Length + 32 + 8 + 32 + 32;

    internal static byte[] GetBindingDigest(TpmNvCounterBinding binding)
    {
        if (binding is null) { throw Failure(); }
        using var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        hash.AppendData(BindingDomain);
        Span<byte> number = stackalloc byte[8];
        BinaryPrimitives.WriteUInt32BigEndian(number, binding.Index);
        hash.AppendData(number[..4]);
        hash.AppendData(binding.ExpectedName);
        hash.AppendData(binding.ExpectedAuthSha256);
        hash.AppendData(binding.ContextSha256);
        BinaryPrimitives.WriteUInt64BigEndian(number, binding.MinimumCounter);
        hash.AppendData(number);
        return hash.GetHashAndReset();
    }

    internal static byte[] DeriveAuthenticationKey(TpmNvCounterBinding binding, ReadOnlySpan<byte> authorization)
    {
        if (binding is null || authorization.Length != 32) { throw Failure(); }
        Span<byte> authHash = stackalloc byte[32];
        try
        {
            SHA256.HashData(authorization, authHash);
            if (!CryptographicOperations.FixedTimeEquals(authHash, binding.ExpectedAuthSha256))
            {
                throw Failure();
            }
            var identity = GetBindingDigest(binding);
            var input = new byte[KeyDomain.Length + identity.Length];
            KeyDomain.CopyTo(input);
            identity.CopyTo(input, KeyDomain.Length);
            return HMACSHA256.HashData(authorization, input);
        }
        finally { CryptographicOperations.ZeroMemory(authHash); }
    }

    internal static byte[] CreateFrame(
        ReadOnlySpan<byte> bindingSha256, ulong counter,
        ReadOnlySpan<byte> stateDigest, ReadOnlySpan<byte> authenticationKey)
    {
        if (bindingSha256.Length != 32 || counter == 0 || stateDigest.Length != 32 || authenticationKey.Length != 32)
        {
            throw Failure();
        }
        var frame = new byte[FrameLength];
        Magic.CopyTo(frame);
        bindingSha256.CopyTo(frame.AsSpan(Magic.Length, 32));
        BinaryPrimitives.WriteUInt64BigEndian(frame.AsSpan(Magic.Length + 32), counter);
        stateDigest.CopyTo(frame.AsSpan(Magic.Length + 40, 32));
        HMACSHA256.HashData(authenticationKey, frame.AsSpan(0, FrameLength - 32), frame.AsSpan(FrameLength - 32));
        return frame;
    }

    internal static ProtectedLocalStateCheckpoint ParseFrame(
        ReadOnlySpan<byte> frame, ReadOnlySpan<byte> bindingSha256,
        ulong expectedCounter, ReadOnlySpan<byte> authenticationKey)
    {
        if (frame.Length != FrameLength || bindingSha256.Length != 32 || authenticationKey.Length != 32 ||
            expectedCounter == 0 || !frame[..Magic.Length].SequenceEqual(Magic) ||
            !CryptographicOperations.FixedTimeEquals(frame.Slice(Magic.Length, 32), bindingSha256) ||
            BinaryPrimitives.ReadUInt64BigEndian(frame[(Magic.Length + 32)..]) != expectedCounter)
        {
            throw Failure();
        }
        Span<byte> expectedMac = stackalloc byte[32];
        try
        {
            HMACSHA256.HashData(authenticationKey, frame[..^32], expectedMac);
            if (!CryptographicOperations.FixedTimeEquals(expectedMac, frame[^32..])) { throw Failure(); }
        }
        finally { CryptographicOperations.ZeroMemory(expectedMac); }
        return new ProtectedLocalStateCheckpoint(bindingSha256, expectedCounter, frame.Slice(Magic.Length + 40, 32));
    }

    internal static CryptographicException Failure() => new("Protected local checkpoint is unavailable.");
}
