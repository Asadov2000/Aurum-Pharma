using System.Security.Cryptography;
using AurumPharma.Desktop.Core.Security;

namespace AurumPharma.Desktop.Core.Tests;

public sealed class WindowsTpmProtectedStateStoreTests
{
    private const string FailureMessage = "Protected local checkpoint is unavailable.";

    [Fact]
    public void ExistingAuthenticatedCheckpointIsReturnedOnlyAfterExclusiveFileCloses()
    {
        var fixture = new Fixture();
        using var store = fixture.Store();
        var checkpoint = store.ReadExisting();
        Assert.Equal(7UL, checkpoint.Counter);
        Assert.Equal(fixture.Digest, checkpoint.StateDigest);
        Assert.Equal("file.close", fixture.Events[^1]);
        Assert.Equal(1, fixture.Storage.Opens);
        Assert.Equal(0, fixture.Counter.Advances);
        Assert.Equal(0, fixture.Storage.Writes);
        Assert.Equal(0, fixture.Storage.ActiveHandles);
    }

    [Fact]
    public void AdvancePersistsAuthenticatedNextStateAfterHardwareAdvanceAndBeforeReturning()
    {
        var fixture = new Fixture();
        using var store = fixture.Store();
        var expected = store.ReadExisting();
        fixture.Events.Clear();
        var nextDigest = Bytes(0x62);
        var next = store.Advance(expected, nextDigest);
        Assert.Equal(8UL, next.Counter);
        Assert.Equal(nextDigest, next.StateDigest);
        Assert.Equal(1, fixture.Counter.Advances);
        Assert.Equal(1, fixture.Storage.Writes);
        Assert.True(fixture.Events.IndexOf("counter.advance") < fixture.Events.IndexOf("file.write-flush"));
        Assert.Equal("file.close", fixture.Events[^1]);
        Assert.Equal(0, fixture.Storage.ActiveHandles);
        var saved = ProtectedLocalStateCheckpointCodec.ParseFrame(fixture.Storage.Frame, fixture.Binding, 8, fixture.Key);
        Assert.Equal(next.StateDigest, saved.StateDigest);
        Assert.Equal(8UL, store.ReadExisting().Counter);
    }

    [Fact]
    public void CallerBuffersAndPublishedDigestsAreOwnedIndependently()
    {
        var fixture = new Fixture();
        var originalKey = fixture.Key.ToArray();
        var originalBinding = fixture.Binding.ToArray();
        using var store = fixture.Store();
        Assert.Equal(originalKey, fixture.Key);
        Assert.Equal(originalBinding, fixture.Binding);
        Array.Clear(fixture.Key);
        Array.Clear(fixture.Binding);
        var first = store.ReadExisting();
        first.StateDigest[0] ^= 0xFF;
        Assert.Equal(fixture.Digest, first.StateDigest);
        var nextDigest = Bytes(0x73);
        var second = store.Advance(first, nextDigest);
        Assert.Equal(Bytes(0x73), nextDigest);
        Array.Clear(nextDigest);
        second.StateDigest[0] ^= 0xFF;
        Assert.Equal(Bytes(0x73), second.StateDigest);
        Assert.DoesNotContain(Convert.ToHexString(second.StateDigest), second.ToString());
    }

    [Theory]
    [InlineData("missing")]
    [InlineData("truncated")]
    [InlineData("trailing")]
    [InlineData("tampered")]
    [InlineData("other-binding")]
    [InlineData("other-key")]
    [InlineData("old-file")]
    [InlineData("counter-rollback")]
    [InlineData("counter-jump")]
    public void UntrustedOrInconsistentExistingStateNeverAdvancesOrWrites(string mutation)
    {
        var fixture = new Fixture();
        switch (mutation)
        {
            case "missing": fixture.Storage.Missing = true; break;
            case "truncated": fixture.Storage.Frame = fixture.Storage.Frame[..^1]; break;
            case "trailing": fixture.Storage.Frame = [.. fixture.Storage.Frame, 0]; break;
            case "tampered": fixture.Storage.Frame[^1] ^= 1; break;
            case "other-binding": fixture.Storage.Frame = Frame(Bytes(0x99), 7, fixture.Digest, fixture.Key); break;
            case "other-key": fixture.Storage.Frame = Frame(fixture.Binding, 7, fixture.Digest, Bytes(0x99)); break;
            case "old-file": fixture.Storage.Frame = Frame(fixture.Binding, 6, fixture.Digest, fixture.Key); break;
            case "counter-rollback": fixture.Counter.Current = 6; break;
            case "counter-jump": fixture.Counter.Current = 8; break;
        }
        using var store = fixture.Store();
        AssertSanitized(() => store.ReadExisting());
        Assert.Equal(0, fixture.Counter.Advances);
        Assert.Equal(0, fixture.Storage.Writes);
        Assert.Equal(0, fixture.Storage.ActiveHandles);
        AssertLatched(store, fixture);
    }

    [Theory]
    [InlineData("file.open")]
    [InlineData("file.read")]
    [InlineData("counter.read")]
    [InlineData("file.close")]
    public void ReadFailuresAreSanitizedAndNeverPermitImplicitRecovery(string stage)
    {
        var fixture = new Fixture();
        fixture.Storage.ThrowAt = stage;
        fixture.Counter.ThrowAt = stage;
        using var store = fixture.Store();
        AssertSanitized(() => store.ReadExisting());
        Assert.Equal(0, fixture.Counter.Advances);
        Assert.Equal(0, fixture.Storage.Writes);
        Assert.Equal(0, fixture.Storage.ActiveHandles);
        fixture.Storage.ThrowAt = null;
        fixture.Counter.ThrowAt = null;
        AssertLatched(store, fixture);
    }

    [Theory]
    [InlineData("stale")]
    [InlineData("future")]
    [InlineData("foreign-binding")]
    [InlineData("changed-digest")]
    public void ExpectedSnapshotMustMatchEntireCurrentCheckpointBeforeAdvancing(string mutation)
    {
        var fixture = new Fixture();
        using var store = fixture.Store();
        store.ReadExisting();
        var binding = mutation == "foreign-binding" ? Bytes(0x99) : fixture.Binding;
        var generation = mutation == "stale" ? 6UL : mutation == "future" ? 8UL : 7UL;
        var digest = mutation == "changed-digest" ? Bytes(0x99) : fixture.Digest;
        var supplied = ProtectedLocalStateCheckpointCodec.ParseFrame(Frame(binding, generation, digest, fixture.Key), binding, generation, fixture.Key);
        AssertSanitized(() => store.Advance(supplied, Bytes(0x62)));
        Assert.Equal(0, fixture.Counter.Advances);
        Assert.Equal(0, fixture.Storage.Writes);
    }

    [Theory]
    [InlineData(0)]
    [InlineData(31)]
    [InlineData(33)]
    [InlineData(4096)]
    public void InvalidNextDigestCannotConsumeCounterOrWrite(int length)
    {
        var fixture = new Fixture();
        using var store = fixture.Store();
        var expected = store.ReadExisting();
        AssertSanitized(() => store.Advance(expected, new byte[length]));
        Assert.Equal(0, fixture.Counter.Advances);
        Assert.Equal(0, fixture.Storage.Writes);
    }

    [Fact]
    public void NullExpectedCannotConsumeCounterOrCreateCheckpoint()
    {
        var fixture = new Fixture();
        using var store = fixture.Store();
        AssertSanitized(() => store.Advance(null!, Bytes(0x62)));
        Assert.Equal(0, fixture.Counter.Advances);
        Assert.Equal(0, fixture.Storage.Writes);
    }

    [Fact]
    public void IdenticalStateIsNotANewProtectedCheckpoint()
    {
        var fixture = new Fixture();
        using var store = fixture.Store();
        var expected = store.ReadExisting();
        AssertSanitized(() => store.Advance(expected, expected.StateDigest));
        Assert.Equal(0, fixture.Counter.Advances);
        Assert.Equal(0, fixture.Storage.Writes);
    }

    [Theory]
    [InlineData("counter-fails-after-increment")]
    [InlineData("write-fails")]
    [InlineData("partial-write")]
    [InlineData("flush-fails")]
    [InlineData("readback-fails")]
    [InlineData("readback-corrupt")]
    [InlineData("close-fails")]
    public void InterruptedAdvanceNeverReturnsSuccessAndCannotRetryInTheSameInstance(string mutation)
    {
        var fixture = new Fixture();
        using var store = fixture.Store();
        var expected = store.ReadExisting();
        switch (mutation)
        {
            case "counter-fails-after-increment": fixture.Counter.ThrowAt = "counter.advance-after"; break;
            case "write-fails": fixture.Storage.ThrowAt = "file.write-before"; break;
            case "partial-write": fixture.Storage.ThrowAt = "file.write-partial"; break;
            case "flush-fails": fixture.Storage.ThrowAt = "file.flush-after"; break;
            case "readback-fails": fixture.Storage.FailReadAfterWrite = true; break;
            case "readback-corrupt": fixture.Storage.CorruptAfterWrite = true; break;
            case "close-fails": fixture.Storage.ThrowAt = "file.close"; break;
        }
        AssertSanitized(() => store.Advance(expected, Bytes(0x62)));
        Assert.Equal(1, fixture.Counter.Advances);
        Assert.Equal(8UL, fixture.Counter.Current);
        Assert.Equal(0, fixture.Storage.ActiveHandles);
        fixture.Counter.ThrowAt = null;
        fixture.Storage.ThrowAt = null;
        AssertLatched(store, fixture);
        Assert.Equal(1, fixture.Counter.Advances);
    }

    [Theory]
    [InlineData("counter.advance-after")]
    [InlineData("file.write-before")]
    [InlineData("file.write-partial")]
    public void ReopeningAfterInterruptedAdvanceRejectsCounterFileGapWithoutRepair(string stage)
    {
        var fixture = new Fixture();
        using (var store = fixture.Store())
        {
            var expected = store.ReadExisting();
            fixture.Counter.ThrowAt = stage;
            fixture.Storage.ThrowAt = stage;
            AssertSanitized(() => store.Advance(expected, Bytes(0x62)));
        }
        var writes = fixture.Storage.Writes;
        fixture.Storage.ThrowAt = null;
        var reopenedCounter = new FakeCounter(fixture.Events) { Current = fixture.Counter.Current };
        using var reopened = new WindowsTpmProtectedStateStore(reopenedCounter, fixture.Storage, fixture.Binding, fixture.Key);
        AssertSanitized(() => reopened.ReadExisting());
        Assert.Equal(0, reopenedCounter.Advances);
        Assert.Equal(writes, fixture.Storage.Writes);
    }

    [Fact]
    public void HardwareAdvanceMustReturnExactlyTheNextCounter()
    {
        var fixture = new Fixture();
        using var store = fixture.Store();
        var expected = store.ReadExisting();
        fixture.Counter.AdvanceAmount = 2;
        AssertSanitized(() => store.Advance(expected, Bytes(0x62)));
        Assert.Equal(1, fixture.Counter.Advances);
        Assert.Equal(0, fixture.Storage.Writes);
    }

    [Fact]
    public void MaximumCounterCannotWrapOrWrite()
    {
        var fixture = new Fixture(ulong.MaxValue);
        using var store = fixture.Store();
        var expected = store.ReadExisting();
        AssertSanitized(() => store.Advance(expected, Bytes(0x62)));
        Assert.Equal(0, fixture.Counter.Advances);
        Assert.Equal(0, fixture.Storage.Writes);
    }

    [Fact]
    public void DisposeIsIdempotentOwnsCounterAndPreventsFileOrHardwareCalls()
    {
        var fixture = new Fixture();
        var store = fixture.Store();
        var expected = store.ReadExisting();
        store.Dispose();
        store.Dispose();
        var calls = fixture.Events.Count;
        AssertSanitized(() => store.ReadExisting());
        AssertSanitized(() => store.Advance(expected, Bytes(0x62)));
        Assert.Equal(calls, fixture.Events.Count);
        Assert.Equal(1, fixture.Counter.Disposals);
        Assert.Equal(0, fixture.Storage.ActiveHandles);
    }

    [Fact]
    public async Task ConcurrentAdvanceOfTheSameCheckpointHasOneWinner()
    {
        var fixture = new Fixture();
        using var store = fixture.Store();
        var expected = store.ReadExisting();
        var release = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        async Task<bool> Attempt(byte value)
        {
            await release.Task;
            try { return store.Advance(expected, Bytes(value)).Counter == 8; }
            catch (CryptographicException error)
            {
                Assert.Equal(FailureMessage, error.Message);
                return false;
            }
        }
        var first = Task.Run(() => Attempt(0x62));
        var second = Task.Run(() => Attempt(0x73));
        release.SetResult(true);
        Assert.Single(await Task.WhenAll(first, second), success => success);
        Assert.Equal(1, fixture.Counter.Advances);
        Assert.Equal(1, fixture.Storage.Writes);
        Assert.Equal(0, fixture.Storage.ActiveHandles);
    }

    [Fact]
    public void CodecAuthenticatesBindingGenerationAndStateAndMakesDefensiveCopies()
    {
        var binding = Bytes(0x21);
        var digest = Bytes(0x31);
        var key = Bytes(0x41);
        var frame = Frame(binding, 0x0102030405060708UL, digest, key);
        var snapshot = ProtectedLocalStateCheckpointCodec.ParseFrame(frame, binding, 0x0102030405060708UL, key);
        Assert.Equal(ProtectedLocalStateCheckpointCodec.FrameLength, frame.Length);
        Array.Clear(frame);
        Array.Clear(binding);
        Array.Clear(digest);
        Array.Clear(key);
        Assert.Equal(0x0102030405060708UL, snapshot.Counter);
        Assert.Equal(Bytes(0x31), snapshot.StateDigest);
        snapshot.StateDigest[0] ^= 1;
        Assert.Equal(Bytes(0x31), snapshot.StateDigest);
    }

    [Fact]
    public void CodecRejectsTamperingAtEveryByteIncludingHeaderAndAuthenticationTag()
    {
        var fixture = new Fixture();
        var original = fixture.Storage.Frame;
        for (var index = 0; index < original.Length; index++)
        {
            var mutated = original.ToArray();
            mutated[index] ^= 1;
            AssertSanitized(() => ProtectedLocalStateCheckpointCodec.ParseFrame(mutated, fixture.Binding, 7, fixture.Key));
        }
    }

    [Theory]
    [InlineData("binding")]
    [InlineData("generation")]
    [InlineData("key")]
    [InlineData("short")]
    [InlineData("long")]
    public void CodecRejectsOtherwiseValidFramesOutsideTheirTrustedContext(string mutation)
    {
        var fixture = new Fixture();
        var frame = fixture.Storage.Frame;
        var binding = fixture.Binding;
        var key = fixture.Key;
        ulong generation = 7;
        switch (mutation)
        {
            case "binding": binding = Bytes(0x99); break;
            case "generation": generation = 8; break;
            case "key": key = Bytes(0x99); break;
            case "short": frame = frame[..^1]; break;
            case "long": frame = [.. frame, 0]; break;
        }
        AssertSanitized(() => ProtectedLocalStateCheckpointCodec.ParseFrame(frame, binding, generation, key));
    }

    [Theory]
    [InlineData("index")]
    [InlineData("name")]
    [InlineData("authorization")]
    [InlineData("context")]
    [InlineData("minimum")]
    public void BindingDigestAndDerivedMacKeyIncludeEveryIndependentlyPinnedField(string mutation)
    {
        uint index = 0x01800001;
        var name = new byte[34];
        name[1] = 0x0B;
        var auth = Bytes(0x51);
        var context = Bytes(0x61);
        ulong minimum = 7;
        var original = new TpmNvCounterBinding(index, name, SHA256.HashData(auth), context, minimum);
        var originalDigest = ProtectedLocalStateCheckpointCodec.GetBindingDigest(original);
        var originalKey = ProtectedLocalStateCheckpointCodec.DeriveAuthenticationKey(original, auth);
        switch (mutation)
        {
            case "index": index++; break;
            case "name": name[^1] ^= 1; break;
            case "authorization": auth[0] ^= 1; break;
            case "context": context[0] ^= 1; break;
            case "minimum": minimum++; break;
        }
        var different = new TpmNvCounterBinding(index, name, SHA256.HashData(auth), context, minimum);
        var changedDigest = ProtectedLocalStateCheckpointCodec.GetBindingDigest(different);
        var changedKey = ProtectedLocalStateCheckpointCodec.DeriveAuthenticationKey(different, auth);
        try
        {
            Assert.Equal(32, originalDigest.Length);
            Assert.Equal(32, originalKey.Length);
            Assert.NotEqual(originalDigest, changedDigest);
            Assert.NotEqual(originalKey, changedKey);
            Assert.Equal(changedKey, ProtectedLocalStateCheckpointCodec.DeriveAuthenticationKey(different, auth));
        }
        finally
        {
            CryptographicOperations.ZeroMemory(originalKey);
            CryptographicOperations.ZeroMemory(changedKey);
            CryptographicOperations.ZeroMemory(auth);
        }
    }

    [Fact]
    public void DerivedMacKeyRejectsAuthorizationThatDoesNotMatchTrustedPin()
    {
        var name = new byte[34];
        name[1] = 0x0B;
        var auth = Bytes(0x51);
        var binding = new TpmNvCounterBinding(0x01800001, name, SHA256.HashData(auth), Bytes(0x61), 7);
        auth[0] ^= 1;
        AssertSanitized(() => ProtectedLocalStateCheckpointCodec.DeriveAuthenticationKey(binding, auth));
    }

    private static byte[] Bytes(byte value) => Enumerable.Repeat(value, 32).ToArray();

    private static byte[] Frame(byte[] binding, ulong counter, byte[] digest, byte[] key)
        => ProtectedLocalStateCheckpointCodec.CreateFrame(binding, counter, digest, key);

    private static void AssertLatched(WindowsTpmProtectedStateStore store, Fixture fixture)
    {
        var expected = ProtectedLocalStateCheckpointCodec.ParseFrame(Frame(fixture.Binding, 7, fixture.Digest, fixture.Key), fixture.Binding, 7, fixture.Key);
        var calls = fixture.Events.Count;
        AssertSanitized(() => store.ReadExisting());
        AssertSanitized(() => store.Advance(expected, Bytes(0x62)));
        Assert.Equal(calls, fixture.Events.Count);
    }

    private static void AssertSanitized(Action action)
    {
        var error = Assert.Throws<CryptographicException>(action);
        Assert.Equal(FailureMessage, error.Message);
        Assert.Null(error.InnerException);
        Assert.Empty(error.Data);
    }

    private sealed class Fixture
    {
        internal List<string> Events { get; } = [];
        internal byte[] Binding { get; } = Bytes(0x21);
        internal byte[] Key { get; } = Bytes(0x41);
        internal byte[] Digest { get; } = Bytes(0x31);
        internal FakeCounter Counter { get; }
        internal FakeStorage Storage { get; }

        internal Fixture(ulong counter = 7)
        {
            Counter = new FakeCounter(Events) { Current = counter };
            Storage = new FakeStorage(Events, Frame(Binding, counter, Digest, Key));
        }

        internal WindowsTpmProtectedStateStore Store() => new(Counter, Storage, Binding, Key);
    }

    private sealed class FakeCounter(List<string> events) : IProtectedStateCounter
    {
        internal ulong Current { get; set; } = 7;
        internal ulong AdvanceAmount { get; set; } = 1;
        internal string? ThrowAt { get; set; }
        internal int Advances { get; private set; }
        internal int Disposals { get; private set; }

        public ulong ReadCurrent()
        {
            Event("counter.read");
            return Current;
        }

        public ulong Advance(ulong expected)
        {
            Assert.Equal(Current, expected);
            Event("counter.advance");
            Advances++;
            Current += AdvanceAmount;
            Event("counter.advance-after");
            return Current;
        }

        public void Dispose()
        {
            Disposals++;
            events.Add("counter.dispose");
        }

        private void Event(string stage)
        {
            events.Add(stage);
            if (ThrowAt == stage) throw new InvalidOperationException("synthetic private counter details");
        }
    }

    private sealed class FakeStorage(List<string> events, byte[] frame) : IProtectedStateCheckpointStorage
    {
        internal byte[] Frame { get; set; } = frame;
        internal string? ThrowAt { get; set; }
        internal bool Missing { get; set; }
        internal bool FailReadAfterWrite { get; set; }
        internal bool CorruptAfterWrite { get; set; }
        internal int Opens { get; private set; }
        internal int Writes { get; private set; }
        internal int ActiveHandles { get; private set; }

        public IProtectedStateCheckpointFile OpenExclusive()
        {
            Event("file.open");
            if (Missing) throw new FileNotFoundException("synthetic private file path");
            Assert.Equal(0, ActiveHandles);
            Opens++;
            ActiveHandles++;
            return new FakeFile(this);
        }

        private void Event(string stage)
        {
            events.Add(stage);
            if (ThrowAt == stage) throw new IOException("synthetic private path and storage diagnostics");
        }

        private sealed class FakeFile(FakeStorage storage) : IProtectedStateCheckpointFile
        {
            private bool disposed;

            public byte[] ReadExact(int length)
            {
                Assert.False(disposed);
                storage.Event("file.read");
                if (storage.FailReadAfterWrite && storage.Writes > 0) throw new IOException("synthetic readback failure");
                Assert.Equal(ProtectedLocalStateCheckpointCodec.FrameLength, length);
                if (storage.Frame.Length != length) throw new IOException("synthetic wrong frame size");
                return storage.Frame.ToArray();
            }

            public void WriteAndFlush(ReadOnlySpan<byte> frame)
            {
                Assert.False(disposed);
                storage.Event("file.write-before");
                storage.Writes++;
                if (storage.ThrowAt == "file.write-partial")
                {
                    storage.Frame = frame[..5].ToArray();
                    storage.Event("file.write-partial");
                }
                storage.Frame = frame.ToArray();
                if (storage.CorruptAfterWrite) storage.Frame[^1] ^= 1;
                storage.Event("file.write-flush");
                storage.Event("file.flush-after");
            }

            public void Dispose()
            {
                Assert.False(disposed);
                disposed = true;
                storage.ActiveHandles--;
                storage.Event("file.close");
            }
        }
    }
}
