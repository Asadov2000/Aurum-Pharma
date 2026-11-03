using System.Security.Cryptography;
using AurumPharma.Desktop.Core.Security;

namespace AurumPharma.Desktop.Core.Tests;

public sealed class ProtectedStateCheckpointFileTests
{
    [Fact]
    public void DurableExistingFileReopensAndRestoringItsOldCopyDenies()
    {
        using var fixture = new CheckpointFiles();
        var previous = File.ReadAllBytes(fixture.Path);
        var nextDigest = SHA256.HashData("next complete protected state"u8);
        using (var store = fixture.Open())
        {
            var before = store.ReadExisting();
            var after = store.Advance(before, nextDigest);
            Assert.Equal(8UL, after.Counter);
            Assert.Equal(nextDigest, after.StateDigest);
        }
        using (var restarted = fixture.Open())
        {
            Assert.Equal(8UL, restarted.ReadExisting().Counter);
            Assert.Equal(nextDigest, restarted.ReadExisting().StateDigest);
        }
        File.WriteAllBytes(fixture.Path, previous);
        using var rolledBack = fixture.Open();
        var error = Assert.Throws<CryptographicException>(() => rolledBack.ReadExisting());
        Assert.Equal("Protected local checkpoint is unavailable.", error.Message);
        Assert.Null(error.InnerException);
        Assert.Equal(8UL, fixture.Counter.Value);
        Assert.Equal(1, fixture.Counter.Writes);
    }

    [Fact]
    public void MissingCheckpointIsNeverCreated()
    {
        using var fixture = new CheckpointFiles();
        File.Delete(fixture.Path);
        Assert.Throws<FileNotFoundException>(() => new ExistingProtectedStateCheckpointFileStorage(fixture.Path));
        Assert.False(File.Exists(fixture.Path));
        Assert.Equal(0, fixture.Counter.Reads);
        Assert.Equal(0, fixture.Counter.Writes);
    }

    [Theory]
    [InlineData(0)]
    [InlineData(1)]
    [InlineData(-1)]
    [InlineData(1_024)]
    [InlineData(65_536)]
    public void IncorrectFileLengthDeniesBeforeHardwareAccess(int length)
    {
        using var fixture = new CheckpointFiles();
        File.WriteAllBytes(fixture.Path, new byte[length < 0 ? ProtectedLocalStateCheckpointCodec.FrameLength - 1 : length]);
        using var store = fixture.Open();
        Assert.Throws<CryptographicException>(() => store.ReadExisting());
        Assert.Equal(0, fixture.Counter.Reads);
        Assert.Equal(0, fixture.Counter.Writes);
        Assert.Throws<CryptographicException>(() => store.ReadExisting());
    }

    [Fact]
    public void DirectoryAndRelativePathsCannotBecomeCheckpoints()
    {
        using var fixture = new CheckpointFiles();
        Assert.Throws<CryptographicException>(() =>
            new ExistingProtectedStateCheckpointFileStorage(System.IO.Path.GetDirectoryName(fixture.Path)!));
        Assert.Throws<CryptographicException>(() => new ExistingProtectedStateCheckpointFileStorage("checkpoint.bin"));
        Assert.Throws<CryptographicException>(() => new ExistingProtectedStateCheckpointFileStorage(@"\\server\checkpoint.bin"));
        if (OperatingSystem.IsWindows())
        {
            Assert.Throws<CryptographicException>(() => new ExistingProtectedStateCheckpointFileStorage(fixture.Path + ":hidden"));
        }
    }

    [Fact]
    public void ExclusiveFileOwnerPreventsASecondStoreFromReachingHardware()
    {
        using var fixture = new CheckpointFiles();
        using var held = new FileStream(fixture.Path, FileMode.Open, FileAccess.ReadWrite, FileShare.None);
        using var store = fixture.Open();
        Assert.Throws<CryptographicException>(() => store.ReadExisting());
        Assert.Equal(0, fixture.Counter.Reads);
        Assert.Equal(0, fixture.Counter.Writes);
    }

    [Fact]
    public async Task IndependentHandlesWithOnePinnedFileCannotCommitTheSameExpectedStateTwice()
    {
        using var fixture = new CheckpointFiles();
        using var first = fixture.Open();
        using var second = fixture.Open();
        var expected = first.ReadExisting();
        var firstDigest = SHA256.HashData("first competing protected state"u8);
        var secondDigest = SHA256.HashData("second competing protected state"u8);
        using var ready = new Barrier(2);
        var results = await Task.WhenAll(
            Task.Run(() => TryAdvance(first, firstDigest)),
            Task.Run(() => TryAdvance(second, secondDigest)));
        Assert.Single(results, succeeded => succeeded);
        Assert.Equal(1, fixture.Counter.Writes);
        Assert.Equal(8UL, fixture.Counter.Value);
        using var reopened = fixture.Open();
        var committed = reopened.ReadExisting();
        Assert.Equal(results[0] ? firstDigest : secondDigest, committed.StateDigest);

        bool TryAdvance(WindowsTpmProtectedStateStore store, byte[] digest)
        {
            ready.SignalAndWait(TimeSpan.FromSeconds(10));
            try { return store.Advance(expected, digest).Counter == 8; }
            catch (CryptographicException) { return false; }
        }
    }

    private sealed class CheckpointFiles : IDisposable
    {
        private readonly string directory;
        private readonly byte[] binding = SHA256.HashData("fictional pinned enrollment"u8);
        private readonly byte[] key = SHA256.HashData("fictional checkpoint key"u8);
        public string Path { get; }
        public SharedCounter Counter { get; } = new();

        public CheckpointFiles()
        {
            // AppContext.BaseDirectory is the project's test output, never Windows Temp.
            directory = System.IO.Path.Combine(AppContext.BaseDirectory, "checkpoint-io-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(directory);
            Path = System.IO.Path.Combine(directory, "state.bin");
            File.WriteAllBytes(Path, ProtectedLocalStateCheckpointCodec.CreateFrame(
                binding, 7, SHA256.HashData("initial complete protected state"u8), key));
        }

        public WindowsTpmProtectedStateStore Open() => new(
            new CounterHandle(Counter), new ExistingProtectedStateCheckpointFileStorage(Path), binding, key);

        public void Dispose()
        {
            File.Delete(Path);
            Directory.Delete(directory, recursive: false);
            CryptographicOperations.ZeroMemory(key);
        }
    }

    private sealed class SharedCounter
    {
        public ulong Value = 7;
        public int Writes;
        public int Reads;
    }

    // Models persistent hardware across new process handles; no TPM commands are submitted.
    private sealed class CounterHandle(SharedCounter state) : IProtectedStateCounter
    {
        private bool disposed;
        public ulong ReadCurrent()
        {
            ObjectDisposedException.ThrowIf(disposed, this);
            lock (state) { state.Reads++; return state.Value; }
        }
        public ulong Advance(ulong expected)
        {
            ObjectDisposedException.ThrowIf(disposed, this);
            lock (state)
            {
                if (state.Value != expected) { throw new CryptographicException(); }
                state.Writes++;
                return ++state.Value;
            }
        }
        public void Dispose() => disposed = true;
    }
}
