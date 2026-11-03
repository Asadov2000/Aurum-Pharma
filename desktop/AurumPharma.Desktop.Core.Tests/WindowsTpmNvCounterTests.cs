using System.Buffers.Binary;
using System.Security.Cryptography;
using AurumPharma.Desktop.Core.Security;

namespace AurumPharma.Desktop.Core.Tests;

public sealed class WindowsTpmNvCounterTests
{
    private const string FailureMessage = "Protected local state counter is unavailable.";
    private const uint Index = 0x01800001;
    private const uint ReadPublic = 0x00000169;
    private const uint Read = 0x0000014E;
    private const uint Increment = 0x00000134;
    private const uint Attributes = 0x22040014;

    [Fact]
    public void OpenReadsExistingPinnedCounterWithoutProvisioningAndClosesBeforePublishing()
    {
        var transport = new FakeTransport();
        using var counter = Open(transport);
        Assert.Equal(["create", "public", "read", "close"], transport.Events);
        Assert.Equal(7UL, counter.ReadCurrent());
        Assert.Equal([ReadPublic, Read, ReadPublic, Read], transport.Codes);
        Assert.Equal(0, transport.Increments);
        Assert.Equal(2, transport.CloseCount);
        Assert.All(transport.CommandReferences, command => Assert.All(command, value => Assert.Equal(0, value)));
    }

    [Fact]
    public void AdvanceChecksCurrentStateIncrementsOnceAndConfirmsBeforeReturning()
    {
        var transport = new FakeTransport();
        using var counter = Open(transport);
        Assert.Equal(8UL, counter.Advance(7));
        Assert.Equal([ReadPublic, Read, ReadPublic, Read, Increment, ReadPublic, Read], transport.Codes);
        Assert.Equal(1, transport.Increments);
        Assert.Equal(8UL, counter.ReadCurrent());
        Assert.Equal(3, transport.CloseCount);
        Assert.All(transport.CommandReferences, command => Assert.All(command, value => Assert.Equal(0, value)));
    }

    [Fact]
    public void BindingCopiesPinnedMetadataAndHasNoSensitiveStringRepresentation()
    {
        var name = Name(PublicArea());
        var authHash = SHA256.HashData(Secret());
        var context = Enumerable.Repeat((byte)0x63, 32).ToArray();
        var expectedName = name.ToArray();
        var expectedAuthHash = authHash.ToArray();
        var expectedContext = context.ToArray();
        var binding = new TpmNvCounterBinding(Index, name, authHash, context, 7);
        Array.Clear(name);
        Array.Clear(authHash);
        Array.Clear(context);
        binding.ExpectedName[0] ^= 0xFF;
        binding.ExpectedAuthSha256[0] ^= 0xFF;
        binding.ContextSha256[0] ^= 0xFF;
        Assert.Equal(expectedName, binding.ExpectedName);
        Assert.Equal(expectedAuthHash, binding.ExpectedAuthSha256);
        Assert.Equal(expectedContext, binding.ContextSha256);
        Assert.Equal(Index, binding.Index);
        Assert.Equal(7UL, binding.MinimumCounter);
        Assert.Equal(nameof(TpmNvCounterBinding), binding.ToString());
    }

    [Theory]
    [InlineData(0U)]
    [InlineData(0x00800001U)]
    [InlineData(0x02000001U)]
    [InlineData(uint.MaxValue)]
    public void BindingRejectsOtherHandleTypes(uint index)
    {
        AssertSanitized(() => new TpmNvCounterBinding(index, Name(PublicArea()), SHA256.HashData(Secret()), new byte[32], 1));
    }

    [Theory]
    [InlineData("name-short")]
    [InlineData("name-long")]
    [InlineData("name-algorithm")]
    [InlineData("auth-short")]
    [InlineData("auth-long")]
    [InlineData("context-short")]
    [InlineData("context-long")]
    [InlineData("minimum-zero")]
    public void BindingRejectsUnboundedOrUninitializedPins(string mutation)
    {
        var name = Name(PublicArea());
        var authHash = SHA256.HashData(Secret());
        var context = new byte[32];
        ulong minimum = 1;
        switch (mutation)
        {
            case "name-short": name = name[..^1]; break;
            case "name-long": name = [.. name, 0]; break;
            case "name-algorithm": name[1] = 0x04; break;
            case "auth-short": authHash = new byte[31]; break;
            case "auth-long": authHash = new byte[33]; break;
            case "context-short": context = new byte[31]; break;
            case "context-long": context = new byte[33]; break;
            case "minimum-zero": minimum = 0; break;
        }
        AssertSanitized(() => new TpmNvCounterBinding(Index, name, authHash, context, minimum));
    }

    [Theory]
    [InlineData(0)]
    [InlineData(31)]
    [InlineData(33)]
    [InlineData(4096)]
    public void InvalidAuthorizationLengthFailsBeforeTouchingHardware(int length)
    {
        var transport = new FakeTransport();
        AssertSanitized(() => WindowsTpmNvCounter.OpenExisting(Binding(), new byte[length], transport));
        Assert.Empty(transport.Events);
    }

    [Fact]
    public void WrongAuthorizationPinNullBindingAndNullTransportFailWithoutHardwareFallback()
    {
        var transport = new FakeTransport();
        var incorrect = Secret();
        incorrect[3] ^= 1;
        AssertSanitized(() => WindowsTpmNvCounter.OpenExisting(Binding(), incorrect, transport));
        AssertSanitized(() => WindowsTpmNvCounter.OpenExisting(null!, Secret(), transport));
        AssertSanitized(() => WindowsTpmNvCounter.OpenExisting(Binding(), Secret(), null!));
        Assert.Empty(transport.Events);
    }

    [Fact]
    public void CounterOwnsAuthorizationAndNeverModifiesCallerSecret()
    {
        var auth = Secret();
        var transport = new FakeTransport();
        using var counter = WindowsTpmNvCounter.OpenExisting(Binding(), auth, transport);
        Assert.Equal(Secret(), auth);
        Array.Clear(auth);
        Assert.Equal(8UL, counter.Advance(7));
        Assert.All(transport.CommandReferences, command => Assert.All(command, value => Assert.Equal(0, value)));
    }

    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public void FailedCreateClosesAnyReturnedContextAndNeverSubmits(bool returnsContext)
    {
        var transport = new FakeTransport { CreateStatus = 0x8028400F, Context = returnsContext ? 42 : 0 };
        AssertSanitized(() => Open(transport));
        Assert.Empty(transport.Codes);
        Assert.Equal(returnsContext ? 1 : 0, transport.CloseCount);
    }

    [Fact]
    public void SuccessfulCreateWithNullHandleCannotSubmit()
    {
        var transport = new FakeTransport { Context = 0 };
        AssertSanitized(() => Open(transport));
        Assert.Equal(["create"], transport.Events);
    }

    [Theory]
    [InlineData("create")]
    [InlineData("public")]
    [InlineData("read")]
    [InlineData("close")]
    public void TransportExceptionsCannotLeakNativeDiagnosticsAndAlwaysClose(string stage)
    {
        var transport = new FakeTransport { ThrowAt = stage };
        AssertSanitized(() => Open(transport));
        Assert.Equal(1, transport.CloseCount);
        Assert.All(transport.CommandReferences, command => Assert.All(command, value => Assert.Equal(0, value)));
    }

    [Theory]
    [InlineData(ReadPublic)]
    [InlineData(Read)]
    public void FailedTbsSubmissionCannotBeOverriddenByValidTpmResponse(uint code)
    {
        var transport = new FakeTransport { FailCode = code };
        AssertSanitized(() => Open(transport));
        Assert.Equal(1, transport.CloseCount);
        Assert.Equal(0, transport.Increments);
    }

    [Fact]
    public void FailedClosePreventsReturningOtherwiseValidCounter()
    {
        var transport = new FakeTransport { CloseStatus = 1 };
        AssertSanitized(() => Open(transport));
        Assert.Equal(1, transport.CloseCount);
    }

    [Theory]
    [InlineData("public", 0U)]
    [InlineData("public", 61U)]
    [InlineData("public", 63U)]
    [InlineData("public", uint.MaxValue)]
    [InlineData("read", 0U)]
    [InlineData("read", 28U)]
    [InlineData("read", 30U)]
    [InlineData("read", uint.MaxValue)]
    public void ResponseLengthIsExactAndBoundedBeforeParsing(string stage, uint length)
    {
        var transport = new FakeTransport { LengthStage = stage, ReturnedLength = length };
        AssertSanitized(() => Open(transport));
        Assert.Equal(1, transport.CloseCount);
    }

    [Theory]
    [InlineData("tag")]
    [InlineData("size-short")]
    [InlineData("size-overflow")]
    [InlineData("tpm-error")]
    [InlineData("public-size")]
    [InlineData("name-size")]
    [InlineData("name-digest")]
    public void MalformedReadPublicResponseCannotEstablishBinding(string mutation)
    {
        var transport = new FakeTransport { MutationStage = "public", Mutation = mutation };
        AssertSanitized(() => Open(transport));
        Assert.Equal([ReadPublic], transport.Codes);
        Assert.Equal(1, transport.CloseCount);
    }

    [Theory]
    [InlineData("index")]
    [InlineData("algorithm")]
    [InlineData("attributes")]
    [InlineData("policy")]
    [InlineData("data-size")]
    public void EvenSelfConsistentNamesCannotAuthorizeDifferentNvPublicSemantics(string mutation)
    {
        var area = PublicArea();
        switch (mutation)
        {
            case "index": area[3] ^= 1; break;
            case "algorithm": area[5] = 0x04; break;
            case "attributes": area[9] ^= 2; break;
            case "policy": area[11] = 1; break;
            case "data-size": area[13] = 16; break;
        }
        var transport = new FakeTransport { Area = area };
        AssertSanitized(() => WindowsTpmNvCounter.OpenExisting(Binding(area), Secret(), transport));
        Assert.Equal([ReadPublic], transport.Codes);
    }

    public static IEnumerable<object[]> ChangedAttributeBits()
        => Enumerable.Range(0, 32).Select(bit => new object[] { 1U << bit });

    [Theory]
    [MemberData(nameof(ChangedAttributeBits))]
    public void NvAttributesMustMatchTheEntireTrustedProfile(uint toggledBit)
    {
        var area = PublicArea(Attributes ^ toggledBit);
        var transport = new FakeTransport { Area = area };
        AssertSanitized(() => WindowsTpmNvCounter.OpenExisting(Binding(area), Secret(), transport));
        Assert.Equal([ReadPublic], transport.Codes);
    }

    [Theory]
    [InlineData("tag")]
    [InlineData("size-short")]
    [InlineData("size-overflow")]
    [InlineData("tpm-error")]
    [InlineData("parameter-size")]
    [InlineData("data-size")]
    [InlineData("nonce")]
    [InlineData("attributes")]
    [InlineData("hmac")]
    public void MalformedAuthenticatedReadResponseIsRejected(string mutation)
    {
        var transport = new FakeTransport { MutationStage = "read", Mutation = mutation };
        AssertSanitized(() => Open(transport));
        Assert.Equal(1, transport.CloseCount);
    }

    [Theory]
    [InlineData(0UL)]
    [InlineData(6UL)]
    public void InitialCounterCannotBeBelowIndependentlyPinnedMinimum(ulong value)
    {
        var transport = new FakeTransport { Counter = value };
        AssertSanitized(() => Open(transport));
        Assert.Equal(0, transport.Increments);
    }

    [Theory]
    [InlineData(6UL)]
    [InlineData(8UL)]
    public void ExternalRollbackAndUnexpectedAdvanceLatchTheOpenedCounter(ulong value)
    {
        var transport = new FakeTransport();
        using var counter = Open(transport);
        transport.Counter = value;
        AssertSanitized(() => counter.ReadCurrent());
        transport.Counter = 7;
        var submissions = transport.Codes.Count;
        AssertSanitized(() => counter.ReadCurrent());
        AssertSanitized(() => counter.Advance(7));
        Assert.Equal(submissions, transport.Codes.Count);
        Assert.Equal(0, transport.Increments);
    }

    [Fact]
    public void ChangedPublicIdentityAfterOpenCannotReachIncrement()
    {
        var transport = new FakeTransport();
        using var counter = Open(transport);
        transport.Area[3] ^= 1;
        AssertSanitized(() => counter.Advance(7));
        Assert.Equal(0, transport.Increments);
        Assert.Equal(ReadPublic, transport.Codes[^1]);
    }

    [Theory]
    [InlineData(0UL)]
    [InlineData(6UL)]
    [InlineData(8UL)]
    public void StaleOrForgedExpectedCounterCannotIncrement(ulong expected)
    {
        var transport = new FakeTransport();
        using var counter = Open(transport);
        AssertSanitized(() => counter.Advance(expected));
        Assert.Equal(0, transport.Increments);
    }

    [Theory]
    [InlineData("submit-fails")]
    [InlineData("submit-throws")]
    [InlineData("response-invalid")]
    [InlineData("post-read-fails")]
    [InlineData("post-read-jumps")]
    [InlineData("close-fails")]
    public void AmbiguousIncrementNeverRetriesOrReturnsAUsableCounter(string mutation)
    {
        var transport = new FakeTransport();
        using var counter = Open(transport);
        switch (mutation)
        {
            case "submit-fails": transport.FailCode = Increment; break;
            case "submit-throws": transport.ThrowAt = "increment"; break;
            case "response-invalid": transport.MutationStage = "increment"; transport.Mutation = "tpm-error"; break;
            case "post-read-fails": transport.FailReadAfterIncrement = true; break;
            case "post-read-jumps": transport.IncrementAmount = 2; break;
            case "close-fails": transport.CloseStatus = 1; break;
        }
        AssertSanitized(() => counter.Advance(7));
        Assert.Equal(1, transport.Increments);
        var submissions = transport.Codes.Count;
        transport.FailCode = 0;
        transport.ThrowAt = null;
        transport.CloseStatus = 0;
        AssertSanitized(() => counter.Advance(7));
        AssertSanitized(() => counter.ReadCurrent());
        Assert.Equal(submissions, transport.Codes.Count);
        Assert.Equal(1, transport.Increments);
        Assert.All(transport.CommandReferences, command => Assert.All(command, value => Assert.Equal(0, value)));
    }

    [Theory]
    [InlineData("tag")]
    [InlineData("size-short")]
    [InlineData("size-overflow")]
    [InlineData("tpm-error")]
    [InlineData("parameter-size")]
    [InlineData("nonce")]
    [InlineData("attributes")]
    [InlineData("hmac")]
    public void IncrementResponseMustBeAnExactAuthenticatedEmptyResponse(string mutation)
    {
        var transport = new FakeTransport();
        using var counter = Open(transport);
        transport.MutationStage = "increment";
        transport.Mutation = mutation;
        AssertSanitized(() => counter.Advance(7));
        Assert.Equal(1, transport.Increments);
    }

    [Fact]
    public void MaximumCounterCannotWrapOrSubmitAnIncrement()
    {
        var transport = new FakeTransport { Counter = ulong.MaxValue };
        using var counter = WindowsTpmNvCounter.OpenExisting(Binding(minimum: ulong.MaxValue), Secret(), transport);
        AssertSanitized(() => counter.Advance(ulong.MaxValue));
        Assert.Equal(0, transport.Increments);
    }

    [Fact]
    public void DisposeIsIdempotentAndPreventsAllHardwareAccess()
    {
        var transport = new FakeTransport();
        var counter = Open(transport);
        counter.Dispose();
        counter.Dispose();
        var submissions = transport.Codes.Count;
        AssertSanitized(() => counter.ReadCurrent());
        AssertSanitized(() => counter.Advance(7));
        Assert.Equal(submissions, transport.Codes.Count);
    }

    [Fact]
    public async Task ConcurrentCompareAndAdvanceHasOnlyOneWinnerOnTheSameCounter()
    {
        var transport = new FakeTransport();
        using var counter = Open(transport);
        using var ready = new CountdownEvent(2);
        using var release = new ManualResetEventSlim();
        Task<bool> Attempt() => Task.Run(() =>
        {
            ready.Signal();
            Assert.True(release.Wait(TimeSpan.FromSeconds(5)));
            try { return counter.Advance(7) == 8; }
            catch (CryptographicException error)
            {
                Assert.Equal(FailureMessage, error.Message);
                return false;
            }
        });
        var first = Attempt();
        var second = Attempt();
        Assert.True(ready.Wait(TimeSpan.FromSeconds(5)));
        release.Set();
        var outcomes = await Task.WhenAll(first, second);
        Assert.Single(outcomes, success => success);
        Assert.Equal(1, transport.Increments);
        Assert.Equal(8UL, transport.Counter);
    }

    [Fact]
    public void PublicFactoryDoesNotTouchNativeApisOnUnsupportedPlatforms()
    {
        if (!OperatingSystem.IsWindows())
        {
            Assert.Throws<PlatformNotSupportedException>(() => WindowsTpmNvCounter.OpenExisting(Binding(), Secret()));
        }
    }

    private static WindowsTpmNvCounter Open(FakeTransport transport)
        => WindowsTpmNvCounter.OpenExisting(Binding(), Secret(), transport);

    private static TpmNvCounterBinding Binding(byte[]? area = null, ulong minimum = 7)
        => new(Index, Name(area ?? PublicArea()), SHA256.HashData(Secret()), new byte[32], minimum);

    private static byte[] Secret() => Enumerable.Repeat((byte)0xA6, 32).ToArray();

    private static byte[] PublicArea(uint attributes = Attributes)
    {
        var area = new byte[14];
        BinaryPrimitives.WriteUInt32BigEndian(area.AsSpan(0, 4), Index);
        BinaryPrimitives.WriteUInt16BigEndian(area.AsSpan(4, 2), 0x000B);
        BinaryPrimitives.WriteUInt32BigEndian(area.AsSpan(6, 4), attributes);
        BinaryPrimitives.WriteUInt16BigEndian(area.AsSpan(12, 2), 8);
        return area;
    }

    private static byte[] Name(byte[] area) => [0, 0x0B, .. SHA256.HashData(area)];

    private static byte[] Response(ushort tag, byte[] body)
    {
        var response = new byte[10 + body.Length];
        BinaryPrimitives.WriteUInt16BigEndian(response.AsSpan(0, 2), tag);
        BinaryPrimitives.WriteUInt32BigEndian(response.AsSpan(2, 4), (uint)response.Length);
        body.CopyTo(response, 10);
        return response;
    }

    private static byte[] PublicResponse(byte[] area) => Response(0x8001, [0, 14, .. area, 0, 34, .. Name(area)]);

    private static byte[] ReadResponse(ulong value)
    {
        var bytes = new byte[8];
        BinaryPrimitives.WriteUInt64BigEndian(bytes, value);
        return Response(0x8002, [0, 0, 0, 10, 0, 8, .. bytes, 0, 0, 1, 0, 0]);
    }

    private static byte[] IncrementResponse() => Response(0x8002, [0, 0, 0, 0, 0, 0, 1, 0, 0]);

    private static void Mutate(byte[] response, string stage, string mutation)
    {
        switch (mutation)
        {
            case "tag": response[1] ^= 3; break;
            case "size-short": BinaryPrimitives.WriteUInt32BigEndian(response.AsSpan(2, 4), (uint)response.Length - 1); break;
            case "size-overflow": BinaryPrimitives.WriteUInt32BigEndian(response.AsSpan(2, 4), uint.MaxValue); break;
            case "tpm-error": BinaryPrimitives.WriteUInt32BigEndian(response.AsSpan(6, 4), 0x0000098E); break;
            case "public-size": response[11] = 13; break;
            case "name-size": response[27] = 33; break;
            case "name-digest": response[^1] ^= 1; break;
            case "parameter-size": response[13] ^= 1; break;
            case "data-size": response[15] = 7; break;
            case "nonce": response[^4] = 1; break;
            case "attributes": response[^3] = 0; break;
            case "hmac": response[^1] = 1; break;
            default: throw new InvalidOperationException($"Unknown synthetic mutation {stage}");
        }
    }

    private static void AssertSanitized(Action action)
    {
        var error = Assert.Throws<CryptographicException>(action);
        Assert.Equal(FailureMessage, error.Message);
        Assert.Null(error.InnerException);
        Assert.Empty(error.Data);
    }

    private sealed class FakeTransport : ITpmNvCounterTransport
    {
        internal List<string> Events { get; } = [];
        internal List<uint> Codes { get; } = [];
        internal List<byte[]> CommandReferences { get; } = [];
        internal byte[] Area { get; init; } = PublicArea();
        internal nint Context { get; init; } = 42;
        internal uint CreateStatus { get; init; }
        internal uint CloseStatus { get; set; }
        internal uint FailCode { get; set; }
        internal string? ThrowAt { get; set; }
        internal string? LengthStage { get; init; }
        internal uint ReturnedLength { get; init; }
        internal string? MutationStage { get; set; }
        internal string Mutation { get; set; } = "";
        internal bool FailReadAfterIncrement { get; set; }
        internal ulong Counter { get; set; } = 7;
        internal ulong IncrementAmount { get; set; } = 1;
        internal int Increments { get; private set; }
        internal int CloseCount { get; private set; }

        public uint Create(out nint context)
        {
            context = Context;
            Event("create");
            return CreateStatus;
        }

        public uint Submit(nint context, byte[] command, byte[] response, ref uint responseLength)
        {
            Assert.Equal(Context, context);
            Assert.Equal((uint)command.Length, BinaryPrimitives.ReadUInt32BigEndian(command.AsSpan(2, 4)));
            var code = BinaryPrimitives.ReadUInt32BigEndian(command.AsSpan(6, 4));
            Assert.Contains(code, new[] { ReadPublic, Read, Increment });
            Assert.Equal(Index, BinaryPrimitives.ReadUInt32BigEndian(command.AsSpan(10, 4)));
            Codes.Add(code);
            CommandReferences.Add(command);
            if (code != ReadPublic)
            {
                Assert.Equal(0x8002, BinaryPrimitives.ReadUInt16BigEndian(command.AsSpan(0, 2)));
                Assert.Equal(Index, BinaryPrimitives.ReadUInt32BigEndian(command.AsSpan(14, 4)));
                Assert.Equal(41U, BinaryPrimitives.ReadUInt32BigEndian(command.AsSpan(18, 4)));
                Assert.Equal(0x40000009U, BinaryPrimitives.ReadUInt32BigEndian(command.AsSpan(22, 4)));
                Assert.Equal(new byte[] { 0, 0, 0, 0, 32 }, command[26..31]);
                Assert.Equal(Secret(), command[31..63]);
                Assert.Equal(code == Read ? 67 : 63, command.Length);
                if (code == Read) Assert.Equal(new byte[] { 0, 8, 0, 0 }, command[63..]);
            }
            else
            {
                Assert.Equal(14, command.Length);
                Assert.Equal(0x8001, BinaryPrimitives.ReadUInt16BigEndian(command.AsSpan(0, 2)));
            }
            var stage = code == ReadPublic ? "public" : code == Read ? "read" : "increment";
            if (code == Increment)
            {
                Increments++;
                Counter += IncrementAmount;
            }
            var payload = code == ReadPublic ? PublicResponse(Area) : code == Read ? ReadResponse(Counter) : IncrementResponse();
            if (stage == MutationStage) Mutate(payload, stage, Mutation);
            payload.CopyTo(response, 0);
            responseLength = stage == LengthStage ? ReturnedLength : (uint)payload.Length;
            Event(stage);
            return code == FailCode || code == Read && FailReadAfterIncrement && Increments > 0 ? 1U : 0U;
        }

        public uint Close(nint context)
        {
            Assert.Equal(Context, context);
            CloseCount++;
            Event("close");
            return CloseStatus;
        }

        private void Event(string stage)
        {
            Events.Add(stage);
            if (stage == ThrowAt)
                throw new InvalidOperationException("synthetic native diagnostic with secret material", new Exception("synthetic inner detail"));
        }
    }
}
