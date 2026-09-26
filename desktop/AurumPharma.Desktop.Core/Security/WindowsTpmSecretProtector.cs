using System.Runtime.CompilerServices;
using System.Runtime.Versioning;
using System.Security.Cryptography;

[assembly: InternalsVisibleTo("AurumPharma.Desktop.Core.Tests")]

namespace AurumPharma.Desktop.Core.Security;

/// <summary>An immutable encrypted envelope. It contains no plaintext secret.</summary>
public sealed class ProtectedLocalSecretEnvelope
{
    private readonly byte[] expectedSpkiSha256;
    private readonly byte[] ciphertext;

    public ProtectedLocalSecretEnvelope(
        int schemaVersion,
        Guid protectionKeyId,
        ReadOnlySpan<byte> expectedSpkiSha256,
        ReadOnlySpan<byte> ciphertext)
    {
        if (schemaVersion != 1 || protectionKeyId == Guid.Empty ||
            expectedSpkiSha256.Length != 32 || ciphertext.Length < 256 ||
            ciphertext.Length > 512 || ciphertext.Length % 8 != 0)
        {
            throw FixedPurposeSecretCodec.Failure();
        }

        SchemaVersion = schemaVersion;
        ProtectionKeyId = protectionKeyId;
        this.expectedSpkiSha256 = expectedSpkiSha256.ToArray();
        this.ciphertext = ciphertext.ToArray();
    }

    public int SchemaVersion { get; }
    public Guid ProtectionKeyId { get; }
    public byte[] ExpectedSpkiSha256 => expectedSpkiSha256.ToArray();
    public byte[] Ciphertext => ciphertext.ToArray();

    internal ReadOnlySpan<byte> SpkiHashSpan => expectedSpkiSha256;
    internal ReadOnlySpan<byte> CiphertextSpan => ciphertext;

    public override string ToString() => nameof(ProtectedLocalSecretEnvelope);
}

/// <summary>
/// Protects exactly one 32-byte local secret with a provisioned TPM RSA machine key.
/// Trusted callers supply the pinned public-key hash and an opaque SHA-256 context digest.
/// This provides confidentiality and context binding, not caller authorization or rollback protection.
/// Anyone with the public key can produce a new envelope; an envelope is not an ownership proof.
/// </summary>
public sealed class WindowsTpmSecretProtector : IDisposable
{
    private readonly FixedPurposeSecretCodec codec;

    private WindowsTpmSecretProtector(FixedPurposeSecretCodec codec) => this.codec = codec;

    /// <summary>Opens only an existing, nonexportable Microsoft Platform Crypto Provider key.</summary>
    public static WindowsTpmSecretProtector OpenExisting(
        Guid protectionKeyId,
        ReadOnlySpan<byte> expectedSpkiSha256)
    {
        if (!OperatingSystem.IsWindows())
        {
            throw new PlatformNotSupportedException("TPM secret protection requires Windows.");
        }

        if (protectionKeyId == Guid.Empty || expectedSpkiSha256.Length != 32)
        {
            throw FixedPurposeSecretCodec.Failure();
        }

        return OpenWindowsKey(protectionKeyId, expectedSpkiSha256);
    }

    [SupportedOSPlatform("windows")]
    private static WindowsTpmSecretProtector OpenWindowsKey(
        Guid protectionKeyId,
        ReadOnlySpan<byte> expectedSpkiSha256)
    {
        RSACng? rsa = null;
        FixedPurposeSecretCodec? candidate = null;
        try
        {
            using var key = CngKey.Open(
                $"aurum-edge-local-secret-v1-{protectionKeyId:D}",
                CngProvider.MicrosoftPlatformCryptoProvider,
                CngKeyOpenOptions.MachineKey | CngKeyOpenOptions.Silent);
            if (key.Provider != CngProvider.MicrosoftPlatformCryptoProvider ||
                key.Algorithm != CngAlgorithm.Rsa || key.AlgorithmGroup != CngAlgorithmGroup.Rsa ||
                !key.IsMachineKey || key.IsEphemeral || key.ExportPolicy != CngExportPolicies.None ||
                key.KeyUsage != CngKeyUsages.Decryption || key.KeySize < 2048 || key.KeySize > 4096)
            {
                throw FixedPurposeSecretCodec.Failure();
            }

            // RSACng duplicates the key handle; disposing the opened CngKey is safe.
            rsa = new RSACng(key);
            candidate = new FixedPurposeSecretCodec(rsa, protectionKeyId, expectedSpkiSha256, ownsRsa: true);
            rsa = null;
            // A successful OAEP decrypt proves private-key availability and provider support,
            // without attempting to export private material or persisting anything.
            candidate.VerifyPrivateKey();
            var protector = new WindowsTpmSecretProtector(candidate);
            candidate = null;
            return protector;
        }
        catch (Exception error) when (error is CryptographicException or ArgumentException or NotSupportedException)
        {
            throw FixedPurposeSecretCodec.Failure();
        }
        finally
        {
            candidate?.Dispose();
            rsa?.Dispose();
        }
    }

    public ProtectedLocalSecretEnvelope Protect(ReadOnlySpan<byte> secret, ReadOnlySpan<byte> contextSha256)
        => codec.Protect(secret, contextSha256);

    /// <summary>The caller owns the returned secret and must zero it after use.</summary>
    public byte[] Unprotect(ProtectedLocalSecretEnvelope envelope, ReadOnlySpan<byte> contextSha256)
        => codec.Unprotect(envelope, contextSha256);

    public void Dispose() => codec.Dispose();
}

// Internal cryptographic codec is the cross-platform test seam, never a production fallback factory.
internal sealed class FixedPurposeSecretCodec : IDisposable
{
    private static ReadOnlySpan<byte> Magic => "AurumPharma/edge-local-secret/v1\0"u8;
    private readonly RSA rsa;
    private readonly Guid protectionKeyId;
    private readonly byte[] spkiHash;
    private readonly bool ownsRsa;
    private readonly object gate = new();
    private bool disposed;

    internal FixedPurposeSecretCodec(RSA rsa, Guid protectionKeyId, ReadOnlySpan<byte> expectedSpkiSha256, bool ownsRsa = false)
    {
        if (protectionKeyId == Guid.Empty || expectedSpkiSha256.Length != 32 ||
            rsa.KeySize < 2048 || rsa.KeySize > 4096 || rsa.KeySize % 64 != 0)
        {
            throw Failure();
        }

        var actualHash = SHA256.HashData(rsa.ExportSubjectPublicKeyInfo());
        if (!CryptographicOperations.FixedTimeEquals(actualHash, expectedSpkiSha256))
        {
            throw Failure();
        }

        this.rsa = rsa;
        this.protectionKeyId = protectionKeyId;
        spkiHash = actualHash;
        this.ownsRsa = ownsRsa;
    }

    internal ProtectedLocalSecretEnvelope Protect(ReadOnlySpan<byte> secret, ReadOnlySpan<byte> contextSha256)
    {
        lock (gate)
        {
            ObjectDisposedException.ThrowIf(disposed, this);
            if (secret.Length != 32 || contextSha256.Length != 32)
            {
                throw Failure();
            }

            Span<byte> frame = stackalloc byte[Magic.Length + 64];
            try
            {
                Magic.CopyTo(frame);
                contextSha256.CopyTo(frame[Magic.Length..]);
                secret.CopyTo(frame[(Magic.Length + 32)..]);
                var ciphertext = rsa.Encrypt(frame, RSAEncryptionPadding.OaepSHA256);
                if (ciphertext.Length != rsa.KeySize / 8)
                {
                    throw Failure();
                }

                return new ProtectedLocalSecretEnvelope(1, protectionKeyId, spkiHash, ciphertext);
            }
            catch (Exception error) when (error is CryptographicException or ArgumentException or NotSupportedException)
            {
                throw Failure();
            }
            finally
            {
                CryptographicOperations.ZeroMemory(frame);
            }
        }
    }

    internal byte[] Unprotect(ProtectedLocalSecretEnvelope envelope, ReadOnlySpan<byte> contextSha256)
    {
        lock (gate)
        {
            ObjectDisposedException.ThrowIf(disposed, this);
            if (envelope is null || contextSha256.Length != 32 || envelope.SchemaVersion != 1 ||
                envelope.ProtectionKeyId != protectionKeyId ||
                !CryptographicOperations.FixedTimeEquals(envelope.SpkiHashSpan, spkiHash) ||
                envelope.CiphertextSpan.Length != rsa.KeySize / 8)
            {
                throw Failure();
            }

            // Caller-owned input is never modified. A fixed destination bounds plaintext
            // and lets us clear the full temporary buffer even on decrypt failures.
            Span<byte> plaintext = stackalloc byte[512];
            try
            {
                if (!rsa.TryDecrypt(envelope.CiphertextSpan, plaintext, RSAEncryptionPadding.OaepSHA256, out var written) ||
                    written != Magic.Length + 64 ||
                    !CryptographicOperations.FixedTimeEquals(plaintext[..Magic.Length], Magic) ||
                    !CryptographicOperations.FixedTimeEquals(plaintext.Slice(Magic.Length, 32), contextSha256))
                {
                    throw Failure();
                }

                return plaintext.Slice(Magic.Length + 32, 32).ToArray();
            }
            catch (Exception error) when (error is CryptographicException or ArgumentException or NotSupportedException)
            {
                throw Failure();
            }
            finally
            {
                CryptographicOperations.ZeroMemory(plaintext);
            }
        }
    }

    internal void VerifyPrivateKey()
    {
        Span<byte> probe = stackalloc byte[32];
        Span<byte> context = stackalloc byte[32];
        byte[]? decrypted = null;
        try
        {
            RandomNumberGenerator.Fill(probe);
            RandomNumberGenerator.Fill(context);
            decrypted = Unprotect(Protect(probe, context), context);
            if (!CryptographicOperations.FixedTimeEquals(decrypted, probe))
            {
                throw Failure();
            }
        }
        finally
        {
            CryptographicOperations.ZeroMemory(probe);
            CryptographicOperations.ZeroMemory(context);
            if (decrypted is not null)
            {
                CryptographicOperations.ZeroMemory(decrypted);
            }
        }
    }

    internal static CryptographicException Failure() => new("Local secret protection failed.");

    public void Dispose()
    {
        lock (gate)
        {
            if (!disposed)
            {
                disposed = true;
                if (ownsRsa)
                {
                    rsa.Dispose();
                }
            }
        }
    }
}
