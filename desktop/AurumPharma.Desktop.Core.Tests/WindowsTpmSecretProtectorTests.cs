using System.Security.Cryptography;
using AurumPharma.Desktop.Core.Security;

namespace AurumPharma.Desktop.Core.Tests;

public sealed class WindowsTpmSecretProtectorTests
{
    [Theory]
    [InlineData(2048)]
    [InlineData(3072)]
    [InlineData(4096)]
    public void FixedPurposeCodecRoundTripsAndUsesRandomizedOaep(int keySize)
    {
        using var rsa = RSA.Create(keySize);
        using var codec = CreateCodec(rsa);
        var secret = RandomNumberGenerator.GetBytes(32);
        var context = RandomNumberGenerator.GetBytes(32);
        var first = codec.Protect(secret, context);
        var second = codec.Protect(secret, context);
        var decrypted = codec.Unprotect(first, context);
        try
        {
            Assert.Equal(secret, decrypted);
            Assert.NotEqual(first.Ciphertext, second.Ciphertext);
            Assert.Equal(keySize / 8, first.Ciphertext.Length);
            codec.VerifyPrivateKey();
        }
        finally
        {
            CryptographicOperations.ZeroMemory(secret);
            CryptographicOperations.ZeroMemory(decrypted);
        }
    }

    [Fact]
    public void EnvelopeMakesDefensiveCopiesAndDoesNotDescribeCiphertext()
    {
        var hash = RandomNumberGenerator.GetBytes(32);
        var ciphertext = RandomNumberGenerator.GetBytes(256);
        var envelope = new ProtectedLocalSecretEnvelope(1, Guid.NewGuid(), hash, ciphertext);
        var expectedHash = hash.ToArray();
        var expectedCiphertext = ciphertext.ToArray();
        hash[0] ^= 1;
        ciphertext[0] ^= 1;
        envelope.ExpectedSpkiSha256[0] ^= 1;
        envelope.Ciphertext[0] ^= 1;

        Assert.Equal(expectedHash, envelope.ExpectedSpkiSha256);
        Assert.Equal(expectedCiphertext, envelope.Ciphertext);
        Assert.Equal(nameof(ProtectedLocalSecretEnvelope), envelope.ToString());
    }

    [Theory]
    [InlineData(0)]
    [InlineData(31)]
    [InlineData(33)]
    [InlineData(512)]
    public void CodecRejectsOtherSecretAndContextSizes(int length)
    {
        using var rsa = RSA.Create(2048);
        using var codec = CreateCodec(rsa);
        var valid = new byte[32];
        AssertSanitized(() => codec.Protect(new byte[length], valid));
        AssertSanitized(() => codec.Protect(valid, new byte[length]));
        var envelope = codec.Protect(valid, valid);
        AssertSanitized(() => codec.Unprotect(envelope, new byte[length]));
    }

    [Fact]
    public void UnprotectRejectsChangedContextMetadataAndCiphertext()
    {
        using var rsa = RSA.Create(2048);
        var keyId = Guid.NewGuid();
        var hash = SHA256.HashData(rsa.ExportSubjectPublicKeyInfo());
        using var codec = new FixedPurposeSecretCodec(rsa, keyId, hash);
        var context = RandomNumberGenerator.GetBytes(32);
        var envelope = codec.Protect(RandomNumberGenerator.GetBytes(32), context);
        var otherContext = context.ToArray();
        otherContext[0] ^= 1;
        var otherHash = hash.ToArray();
        otherHash[0] ^= 1;
        var damaged = envelope.Ciphertext;
        damaged[0] ^= 1;

        AssertSanitized(() => codec.Unprotect(envelope, otherContext));
        AssertSanitized(() => codec.Unprotect(new ProtectedLocalSecretEnvelope(1, Guid.NewGuid(), hash, envelope.Ciphertext), context));
        AssertSanitized(() => codec.Unprotect(new ProtectedLocalSecretEnvelope(1, keyId, otherHash, envelope.Ciphertext), context));
        AssertSanitized(() => codec.Unprotect(new ProtectedLocalSecretEnvelope(1, keyId, hash, damaged), context));
        AssertSanitized(() => codec.Unprotect(new ProtectedLocalSecretEnvelope(1, keyId, hash, new byte[512]), context));
        AssertSanitized(() => codec.Unprotect(null!, context));
    }

    [Theory]
    [InlineData("domain")]
    [InlineData("short")]
    [InlineData("long")]
    [InlineData("padding")]
    public void UnprotectRejectsWellEncryptedButInvalidFrames(string mutation)
    {
        using var rsa = RSA.Create(2048);
        var keyId = Guid.NewGuid();
        var hash = SHA256.HashData(rsa.ExportSubjectPublicKeyInfo());
        using var codec = new FixedPurposeSecretCodec(rsa, keyId, hash);
        var context = RandomNumberGenerator.GetBytes(32);
        var envelope = codec.Protect(RandomNumberGenerator.GetBytes(32), context);
        var frame = rsa.Decrypt(envelope.Ciphertext, RSAEncryptionPadding.OaepSHA256);
        try
        {
            var invalid = mutation switch
            {
                "short" => frame[..^1],
                "long" => [.. frame, (byte)0],
                _ => frame.ToArray(),
            };
            try
            {
                if (mutation == "domain")
                {
                    invalid[0] ^= 1;
                }

                var padding = mutation == "padding" ? RSAEncryptionPadding.OaepSHA1 : RSAEncryptionPadding.OaepSHA256;
                var encrypted = rsa.Encrypt(invalid, padding);
                var malformed = new ProtectedLocalSecretEnvelope(1, keyId, hash, encrypted);
                AssertSanitized(() => codec.Unprotect(malformed, context));
            }
            finally
            {
                CryptographicOperations.ZeroMemory(invalid);
            }
        }
        finally
        {
            CryptographicOperations.ZeroMemory(frame);
        }
    }

    [Fact]
    public void EnvelopeRejectsUnknownVersionAndUnboundedInputs()
    {
        var id = Guid.NewGuid();
        AssertSanitized(() => new ProtectedLocalSecretEnvelope(2, id, new byte[32], new byte[256]));
        AssertSanitized(() => new ProtectedLocalSecretEnvelope(1, Guid.Empty, new byte[32], new byte[256]));
        AssertSanitized(() => new ProtectedLocalSecretEnvelope(1, id, new byte[31], new byte[256]));
        foreach (var length in new[] { 0, 255, 257, 513, 4096 })
        {
            AssertSanitized(() => new ProtectedLocalSecretEnvelope(1, id, new byte[32], new byte[length]));
        }
    }

    [Fact]
    public void CodecRejectsWrongPublicKeyPinWeakKeyAndPublicOnlyPrivateKeyProbe()
    {
        using var rsa = RSA.Create(2048);
        AssertSanitized(() => new FixedPurposeSecretCodec(rsa, Guid.NewGuid(), new byte[32]));
        using var weakRsa = RSA.Create(1024);
        AssertSanitized(() => CreateCodec(weakRsa));
        using var publicOnly = RSA.Create();
        publicOnly.ImportSubjectPublicKeyInfo(rsa.ExportSubjectPublicKeyInfo(), out _);
        using var codec = CreateCodec(publicOnly);
        AssertSanitized(codec.VerifyPrivateKey);
    }

    [Fact]
    public void DisposeIsIdempotentAndRespectsRsaOwnership()
    {
        using var borrowed = RSA.Create(2048);
        var codec = CreateCodec(borrowed);
        codec.Dispose();
        codec.Dispose();
        Assert.Throws<ObjectDisposedException>(() => codec.Protect(new byte[32], new byte[32]));
        Assert.NotEmpty(borrowed.ExportSubjectPublicKeyInfo());
        var owned = RSA.Create(2048);
        var owner = new FixedPurposeSecretCodec(owned, Guid.NewGuid(), SHA256.HashData(owned.ExportSubjectPublicKeyInfo()), ownsRsa: true);
        owner.Dispose();
        Assert.Throws<ObjectDisposedException>(() => owned.ExportSubjectPublicKeyInfo());
    }

    [Fact]
    public void ProductionFactoryFailsClosedForMissingProvisionedKeyOrUnsupportedPlatform()
    {
        // A fresh ID cannot name a provisioned key. Opening it is read-only and must
        // fail rather than implicitly creating a key or falling back to software.
        var missingKeyId = Guid.NewGuid();
        if (OperatingSystem.IsWindows())
        {
            AssertSanitized(() => WindowsTpmSecretProtector.OpenExisting(missingKeyId, new byte[32]));
        }
        else
        {
            Assert.Throws<PlatformNotSupportedException>(() => WindowsTpmSecretProtector.OpenExisting(missingKeyId, new byte[32]));
        }
    }

    private static FixedPurposeSecretCodec CreateCodec(RSA rsa)
        => new(rsa, Guid.NewGuid(), SHA256.HashData(rsa.ExportSubjectPublicKeyInfo()));

    private static void AssertSanitized(Action operation)
    {
        var error = Assert.Throws<CryptographicException>(operation);
        Assert.Equal("Local secret protection failed.", error.Message);
        Assert.Null(error.InnerException);
    }
}
