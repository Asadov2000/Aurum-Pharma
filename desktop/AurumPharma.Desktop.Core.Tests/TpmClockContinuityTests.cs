using AurumPharma.Desktop.Core.Security;

namespace AurumPharma.Desktop.Core.Tests;

public sealed class TpmClockContinuityTests
{
    [Fact]
    public void DefaultObservationRequiresReanchor()
    {
        Assert.Equal(TpmClockContinuityObservation.ReanchorRequired, default(TpmClockContinuityObservation));
    }

    [Theory]
    [InlineData(100UL, 200UL, 100UL, 200UL)]
    [InlineData(100UL, 200UL, 101UL, 200UL)]
    [InlineData(100UL, 200UL, 100UL, 201UL)]
    [InlineData(100UL, 200UL, 101UL, 201UL)]
    [InlineData(0UL, 0UL, 0UL, 0UL)]
    [InlineData(0UL, 0UL, ulong.MaxValue, ulong.MaxValue)]
    [InlineData(0x7FFFFFFFFFFFFFFFUL, 0x7FFFFFFFFFFFFFFFUL, 0x8000000000000000UL, 0x8000000000000000UL)]
    [InlineData(ulong.MaxValue, ulong.MaxValue, ulong.MaxValue, ulong.MaxValue)]
    public void EqualOrIncreasingUnsignedClocksPreserveRawContinuity(
        ulong previousTime, ulong previousClock, ulong currentTime, ulong currentClock)
    {
        Assert.Equal(
            TpmClockContinuityObservation.RawContinuityObserved,
            TpmClockContinuity.Evaluate(Reading(previousTime, previousClock), Reading(currentTime, currentClock)));
    }

    [Theory]
    [InlineData(99UL, 201UL)]
    [InlineData(101UL, 199UL)]
    [InlineData(99UL, 199UL)]
    public void EitherClockRollbackRequiresReanchor(ulong currentTime, ulong currentClock)
    {
        Assert.Equal(
            TpmClockContinuityObservation.ReanchorRequired,
            TpmClockContinuity.Evaluate(Reading(100, 200), Reading(currentTime, currentClock)));
    }

    [Theory]
    [InlineData(ulong.MaxValue, 0UL)]
    [InlineData(0x8000000000000000UL, 0x7FFFFFFFFFFFFFFFUL)]
    public void UnsignedRollbackCannotBeHiddenBySignedConversion(ulong previous, ulong current)
    {
        Assert.Equal(TpmClockContinuityObservation.ReanchorRequired,
            TpmClockContinuity.Evaluate(Reading(previous, 0), Reading(current, 1)));
        Assert.Equal(TpmClockContinuityObservation.ReanchorRequired,
            TpmClockContinuity.Evaluate(Reading(0, previous), Reading(1, current)));
    }

    [Theory]
    [InlineData(1U, 2U, 2U, 2U)]
    [InlineData(1U, 2U, 1U, 3U)]
    [InlineData(1U, 2U, 0U, 2U)]
    [InlineData(1U, 2U, 1U, 1U)]
    [InlineData(uint.MaxValue, 2U, 0U, 2U)]
    [InlineData(1U, uint.MaxValue, 1U, 0U)]
    public void ChangedResetOrRestartCountIncludingRolloverRequiresReanchor(
        uint previousReset, uint previousRestart, uint currentReset, uint currentRestart)
    {
        Assert.Equal(TpmClockContinuityObservation.ReanchorRequired,
            TpmClockContinuity.Evaluate(
                Reading(100, 200, previousReset, previousRestart),
                Reading(101, 201, currentReset, currentRestart)));
    }

    [Fact]
    public void UnchangedMaximumCountersPreserveRawContinuity()
    {
        Assert.Equal(TpmClockContinuityObservation.RawContinuityObserved,
            TpmClockContinuity.Evaluate(
                Reading(100, 200, uint.MaxValue, uint.MaxValue),
                Reading(101, 201, uint.MaxValue, uint.MaxValue)));
    }

    [Theory]
    [InlineData(null, true)]
    [InlineData(true, null)]
    [InlineData(null, null)]
    [InlineData(false, true)]
    [InlineData(true, false)]
    [InlineData(false, false)]
    [InlineData(null, false)]
    [InlineData(false, null)]
    public void MissingOrUnsafeEvidenceOnEitherSideRequiresReanchor(bool? previousSafe, bool? currentSafe)
    {
        Assert.Equal(TpmClockContinuityObservation.ReanchorRequired,
            TpmClockContinuity.Evaluate(
                previousSafe.HasValue ? Reading(100, 200, safe: previousSafe.Value) : null,
                currentSafe.HasValue ? Reading(101, 201, safe: currentSafe.Value) : null));
    }

    [Theory]
    [InlineData(199UL, 301UL)]
    [InlineData(201UL, 299UL)]
    public void MonitorComparesAgainstLatestSuccessfulReading(ulong rollbackTime, ulong rollbackClock)
    {
        var monitor = new TpmClockContinuityMonitor(Reading(100, 200));

        Assert.Equal(TpmClockContinuityObservation.RawContinuityObserved, monitor.Observe(Reading(200, 300)));
        // Both values exceed the baseline, but one falls below the last observation.
        Assert.Equal(TpmClockContinuityObservation.ReanchorRequired,
            monitor.Observe(Reading(rollbackTime, rollbackClock)));
        Assert.Equal(TpmClockContinuityObservation.ReanchorRequired, monitor.Observe(Reading(300, 400)));
    }

    [Fact]
    public void MonitorAcceptsRepeatedAndIncreasingReadings()
    {
        var monitor = new TpmClockContinuityMonitor(Reading(0, 0));

        Assert.Equal(TpmClockContinuityObservation.RawContinuityObserved, monitor.Observe(Reading(0, 0)));
        Assert.Equal(TpmClockContinuityObservation.RawContinuityObserved, monitor.Observe(Reading(1, 0)));
        Assert.Equal(TpmClockContinuityObservation.RawContinuityObserved, monitor.Observe(Reading(1, 1)));
        Assert.Equal(TpmClockContinuityObservation.RawContinuityObserved,
            monitor.Observe(Reading(ulong.MaxValue, ulong.MaxValue)));
        Assert.Equal(TpmClockContinuityObservation.RawContinuityObserved,
            monitor.Observe(Reading(ulong.MaxValue, ulong.MaxValue)));
    }

    [Theory]
    [InlineData("missing")]
    [InlineData("unsafe")]
    [InlineData("reset")]
    [InlineData("restart")]
    public void BrokenObservationCannotBeRepairedWithinTheSameMonitor(string failure)
    {
        var monitor = new TpmClockContinuityMonitor(Reading(100, 200));
        var broken = failure switch
        {
            "missing" => null,
            "unsafe" => Reading(101, 201, safe: false),
            "reset" => Reading(101, 201, reset: 2),
            "restart" => Reading(101, 201, restart: 3),
            _ => throw new ArgumentOutOfRangeException(nameof(failure)),
        };

        Assert.Equal(TpmClockContinuityObservation.ReanchorRequired, monitor.Observe(broken));
        Assert.Equal(TpmClockContinuityObservation.ReanchorRequired, monitor.Observe(Reading(100, 200)));
        Assert.Equal(TpmClockContinuityObservation.ReanchorRequired, monitor.Observe(Reading(300, 400)));

        // A fresh object only observes raw continuity; this is not trusted recovery.
        var freshObserver = new TpmClockContinuityMonitor(Reading(300, 400));
        Assert.Equal(TpmClockContinuityObservation.RawContinuityObserved, freshObserver.Observe(Reading(301, 401)));
    }

    [Theory]
    [InlineData(null)]
    [InlineData(false)]
    public void InvalidBaselineCannotBeRepairedByLaterEvidence(bool? safe)
    {
        var monitor = new TpmClockContinuityMonitor(safe.HasValue ? Reading(100, 200, safe: safe.Value) : null);

        Assert.Equal(TpmClockContinuityObservation.ReanchorRequired, monitor.Observe(Reading(101, 201)));
        Assert.Equal(TpmClockContinuityObservation.ReanchorRequired, monitor.Observe(Reading(102, 202)));
    }

    [Fact]
    public void ExplicitInvalidationIsIdempotentAndCannotBeRestoredByObservation()
    {
        var monitor = new TpmClockContinuityMonitor(Reading(100, 200));
        Assert.Equal(TpmClockContinuityObservation.RawContinuityObserved, monitor.Observe(Reading(101, 201)));

        monitor.Invalidate();
        monitor.Invalidate();

        Assert.Equal(TpmClockContinuityObservation.ReanchorRequired, monitor.Observe(Reading(102, 202)));
        Assert.Equal(TpmClockContinuityObservation.ReanchorRequired, monitor.Observe(Reading(103, 203)));
    }

    private static TpmClockReading Reading(
        ulong time, ulong clock, uint reset = 1, uint restart = 2, bool safe = true) =>
        new(time, clock, reset, restart, safe);
}
