using System.Buffers.Binary;
using System.Security.Cryptography;
using AurumPharma.Desktop.Core.Security;

namespace AurumPharma.Desktop.Core.Tests;

public sealed class WindowsTpmClockReaderTests
{
    private const string FailureMessage = "Offline TPM clock is unavailable";
    private static readonly byte[] ExpectedCommand = [0x80, 0x01, 0, 0, 0, 0x0A, 0, 0, 0x01, 0x81];

    [Fact]
    public void CommandIsExactlyReadClockAndEachCallerOwnsItsBuffer()
    {
        var first = TpmClockCodec.CreateReadClockCommand();
        var second = TpmClockCodec.CreateReadClockCommand();
        Assert.Equal(ExpectedCommand, first);
        Assert.Equal(ExpectedCommand, second);
        Assert.NotSame(first, second);
        Array.Fill(first, (byte)0xFF);
        Assert.Equal(ExpectedCommand, second);
        Assert.Equal(ExpectedCommand, TpmClockCodec.CreateReadClockCommand());
    }

    [Fact]
    public void ParserReadsIndependentBigEndianFieldsWithoutConvertingToUtc()
    {
        // Deliberately asymmetric bytes distinguish width, offset and byte order mistakes.
        byte[] response = [
            0x80, 0x01, 0, 0, 0, 0x23, 0, 0, 0, 0,
            0x01, 0x23, 0x45, 0x67, 0x89, 0xAB, 0xCD, 0xEF,
            0xFE, 0xDC, 0xBA, 0x98, 0x76, 0x54, 0x32, 0x10,
            0x13, 0x57, 0x9B, 0xDF, 0x24, 0x68, 0xAC, 0xE0, 0x01,
        ];
        var reading = TpmClockCodec.Parse(response);
        Array.Clear(response);

        Assert.Equal(0x0123456789ABCDEFUL, reading.TimeMilliseconds);
        Assert.Equal(0xFEDCBA9876543210UL, reading.ClockMilliseconds);
        Assert.Equal(0x13579BDFU, reading.ResetCount);
        Assert.Equal(0x2468ACE0U, reading.RestartCount);
        Assert.True(reading.Safe);
    }

    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public void SafeBitIsReturnedAsRawEvidenceInsteadOfAuthorizingOrRejecting(bool safe)
    {
        var reading = TpmClockCodec.Parse(Response(safe: safe));
        Assert.Equal(safe, reading.Safe);
    }

    [Fact]
    public void ParserPreservesZeroAndUnsignedMaximumValues()
    {
        var zero = TpmClockCodec.Parse(Response(0, 0, 0, 0));
        Assert.Equal(0UL, zero.TimeMilliseconds);
        Assert.Equal(0UL, zero.ClockMilliseconds);
        Assert.Equal(0U, zero.ResetCount);
        Assert.Equal(0U, zero.RestartCount);

        var maximum = TpmClockCodec.Parse(Response(ulong.MaxValue, ulong.MaxValue, uint.MaxValue, uint.MaxValue));
        Assert.Equal(ulong.MaxValue, maximum.TimeMilliseconds);
        Assert.Equal(ulong.MaxValue, maximum.ClockMilliseconds);
        Assert.Equal(uint.MaxValue, maximum.ResetCount);
        Assert.Equal(uint.MaxValue, maximum.RestartCount);
    }

    [Theory]
    [InlineData(0)]
    [InlineData(9)]
    [InlineData(10)]
    [InlineData(34)]
    [InlineData(36)]
    [InlineData(4096)]
    public void ParserRejectsTruncationAndTrailingBytes(int length)
    {
        var response = Response();
        Array.Resize(ref response, length);
        AssertSanitized(() => TpmClockCodec.Parse(response));
    }

    [Theory]
    [InlineData("tag-zero")]
    [InlineData("tag-sessions")]
    [InlineData("size-short")]
    [InlineData("size-long")]
    [InlineData("tpm-error")]
    [InlineData("safe-invalid")]
    public void ParserRejectsMalformedHeaderAndNonBooleanSafe(string mutation)
    {
        var response = Response();
        switch (mutation)
        {
            case "tag-zero":
                response[0] = 0;
                response[1] = 0;
                break;
            case "tag-sessions":
                response[1] = 2;
                break;
            case "size-short":
                BinaryPrimitives.WriteUInt32BigEndian(response.AsSpan(2, 4), 34);
                break;
            case "size-long":
                BinaryPrimitives.WriteUInt32BigEndian(response.AsSpan(2, 4), uint.MaxValue);
                break;
            case "tpm-error":
                BinaryPrimitives.WriteUInt32BigEndian(response.AsSpan(6, 4), 0x00000100);
                break;
            case "safe-invalid":
                response[34] = 2;
                break;
        }

        AssertSanitized(() => TpmClockCodec.Parse(response));
    }

    [Fact]
    public void ReaderUsesFixedCommandAndReturnsOnlyAfterClosingContext()
    {
        var transport = new FakeTransport();
        var result = WindowsTpmClockReader.Read(transport);
        Assert.Equal(123UL, result.TimeMilliseconds);
        Assert.Equal(456UL, result.ClockMilliseconds);
        Assert.Equal(7U, result.ResetCount);
        Assert.Equal(8U, result.RestartCount);
        Assert.Equal(["create", "submit", "close"], transport.Events);
        Assert.Equal(ExpectedCommand, transport.SubmittedCommand);
        Assert.Equal(transport.Context, transport.ClosedContext);
        Assert.Equal(35U, transport.InitialResponseLength);
    }

    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public void FailedCreateClosesAnyReturnedContextWithoutSubmitting(bool returnsContext)
    {
        var transport = new FakeTransport { CreateStatus = 0x8028400F, Context = returnsContext ? 42 : 0 };
        AssertSanitized(() => WindowsTpmClockReader.Read(transport));
        Assert.Equal(returnsContext ? ["create", "close"] : ["create"], transport.Events);
    }

    [Fact]
    public void SuccessfulCreateWithNullHandleStillFailsBeforeSubmit()
    {
        var transport = new FakeTransport { Context = 0 };
        AssertSanitized(() => WindowsTpmClockReader.Read(transport));
        Assert.Equal(["create"], transport.Events);
    }

    [Theory]
    [InlineData("create")]
    [InlineData("submit")]
    [InlineData("close")]
    public void NativeExceptionsAreSanitizedAndReturnedHandleIsClosedOnce(string stage)
    {
        var transport = new FakeTransport { ThrowAt = stage };
        AssertSanitized(() => WindowsTpmClockReader.Read(transport));
        Assert.Equal(1, transport.Events.Count(item => item == "close"));
        Assert.Equal(transport.Context, transport.ClosedContext);
        if (stage == "create")
        {
            Assert.DoesNotContain("submit", transport.Events);
        }
    }

    [Theory]
    [InlineData(1U)]
    [InlineData(0x80284010U)]
    public void TbsSubmitFailureCannotBeOverriddenByValidTpmResponse(uint status)
    {
        var transport = new FakeTransport { SubmitStatus = status };
        AssertSanitized(() => WindowsTpmClockReader.Read(transport));
        Assert.Equal(["create", "submit", "close"], transport.Events);
    }

    [Fact]
    public void FailedClosePreventsReturningOtherwiseValidReading()
    {
        var transport = new FakeTransport { CloseStatus = 0x80284001 };
        AssertSanitized(() => WindowsTpmClockReader.Read(transport));
        Assert.Equal(["create", "submit", "close"], transport.Events);
    }

    [Theory]
    [InlineData(0U)]
    [InlineData(34U)]
    [InlineData(36U)]
    [InlineData(uint.MaxValue)]
    public void ReaderRespectsActualReturnedLengthAndRejectsOverflow(uint length)
    {
        var transport = new FakeTransport { ReturnedLength = length };
        AssertSanitized(() => WindowsTpmClockReader.Read(transport));
        Assert.Equal(["create", "submit", "close"], transport.Events);
    }

    [Fact]
    public void MalformedTpmResponseStillClosesSuccessfullyCreatedContext()
    {
        var transport = new FakeTransport();
        transport.Payload[34] = 0xFF;
        AssertSanitized(() => WindowsTpmClockReader.Read(transport));
        Assert.Equal(["create", "submit", "close"], transport.Events);
    }

    [Fact]
    public void EachReadGetsAnUnmodifiedCommandEvenAfterTransportMutatesPriorBuffer()
    {
        var first = new FakeTransport { MutateCommand = true };
        WindowsTpmClockReader.Read(first);
        var second = new FakeTransport();
        WindowsTpmClockReader.Read(second);
        Assert.Equal(ExpectedCommand, first.SubmittedCommand);
        Assert.Equal(ExpectedCommand, second.SubmittedCommand);
    }

    [Fact]
    public void NullTransportAndFailureFactoryExposeOnlyConstantDiagnostic()
    {
        AssertSanitized(() => WindowsTpmClockReader.Read(null!));
        var error = TpmClockCodec.Failure();
        Assert.Equal(FailureMessage, error.Message);
        Assert.Null(error.InnerException);
        Assert.Empty(error.Data);
    }

    [Fact]
    public void PublicReaderRejectsUnsupportedOperatingSystemWithoutHardwareAccess()
    {
        if (!OperatingSystem.IsWindows())
        {
            Assert.Throws<PlatformNotSupportedException>(() => WindowsTpmClockReader.Read());
        }
    }

    private static byte[] Response(ulong time = 123, ulong clock = 456, uint reset = 7, uint restart = 8, bool safe = true)
    {
        var response = new byte[35];
        response[0] = 0x80;
        response[1] = 0x01;
        BinaryPrimitives.WriteUInt32BigEndian(response.AsSpan(2, 4), 35);
        BinaryPrimitives.WriteUInt64BigEndian(response.AsSpan(10, 8), time);
        BinaryPrimitives.WriteUInt64BigEndian(response.AsSpan(18, 8), clock);
        BinaryPrimitives.WriteUInt32BigEndian(response.AsSpan(26, 4), reset);
        BinaryPrimitives.WriteUInt32BigEndian(response.AsSpan(30, 4), restart);
        response[34] = safe ? (byte)1 : (byte)0;
        return response;
    }

    private static void AssertSanitized(Action action)
    {
        var error = Assert.Throws<CryptographicException>(action);
        Assert.Equal(FailureMessage, error.Message);
        Assert.Null(error.InnerException);
        Assert.Empty(error.Data);
    }

    private sealed class FakeTransport : ITpmClockTransport
    {
        internal List<string> Events { get; } = [];
        internal byte[] Payload { get; } = Response();
        internal nint Context { get; init; } = 42;
        internal uint CreateStatus { get; init; }
        internal uint SubmitStatus { get; init; }
        internal uint CloseStatus { get; init; }
        internal uint ReturnedLength { get; init; } = 35;
        internal string? ThrowAt { get; init; }
        internal bool MutateCommand { get; init; }
        internal byte[]? SubmittedCommand { get; private set; }
        internal uint InitialResponseLength { get; private set; }
        internal nint ClosedContext { get; private set; }

        public uint Create(out nint context)
        {
            context = Context;
            Event("create");
            return CreateStatus;
        }

        public uint Submit(nint context, byte[] command, byte[] response, ref uint responseLength)
        {
            Assert.Equal(Context, context);
            Assert.Equal(35, response.Length);
            InitialResponseLength = responseLength;
            SubmittedCommand = command.ToArray();
            if (MutateCommand)
            {
                Array.Fill(command, (byte)0xFF);
            }

            Payload.CopyTo(response, 0);
            responseLength = ReturnedLength;
            Event("submit");
            return SubmitStatus;
        }

        public uint Close(nint context)
        {
            ClosedContext = context;
            Event("close");
            return CloseStatus;
        }

        private void Event(string name)
        {
            Events.Add(name);
            if (ThrowAt == name)
            {
                throw new InvalidOperationException("synthetic native private diagnostic", new Exception("synthetic inner diagnostic"));
            }
        }
    }
}
