namespace AurumPharma.Desktop.Core.Security;

/// <summary>An observation of raw counters, never UTC or permission to log in.</summary>
public enum TpmClockContinuityObservation
{
    ReanchorRequired,
    RawContinuityObserved
}

/// <summary>
/// Rejects contradictory TPM observations. A positive observation does not prove
/// device identity, continuous power, absence of Clear, or resistance to replay.
/// </summary>
public static class TpmClockContinuity
{
    public static TpmClockContinuityObservation Evaluate(
        TpmClockReading? previous,
        TpmClockReading? current)
    {
        if (previous is null || current is null || !previous.Safe || !current.Safe ||
            previous.ResetCount != current.ResetCount ||
            previous.RestartCount != current.RestartCount ||
            current.TimeMilliseconds < previous.TimeMilliseconds ||
            current.ClockMilliseconds < previous.ClockMilliseconds)
        {
            return TpmClockContinuityObservation.ReanchorRequired;
        }

        // Equality is possible for two reads in one millisecond. No subtraction,
        // signed conversion or UTC projection is needed to compare raw counters.
        return TpmClockContinuityObservation.RawContinuityObserved;
    }
}

/// <summary>
/// Ephemeral, serialized high-water observation with irreversible invalidation.
/// This is not durable anti-rollback storage or a trusted-clock adapter. A future
/// owner must invalidate on failed reads, process/lifecycle gaps and suspend;
/// creating another instance is not a trusted reanchor or recovery procedure.
/// </summary>
internal sealed class TpmClockContinuityMonitor
{
    private readonly object gate = new();
    private TpmClockReading? previous;
    private bool invalidated;

    internal TpmClockContinuityMonitor(TpmClockReading? baseline)
    {
        previous = baseline;
        invalidated = TpmClockContinuity.Evaluate(baseline, baseline) ==
            TpmClockContinuityObservation.ReanchorRequired;
    }

    internal TpmClockContinuityObservation Observe(TpmClockReading? current)
    {
        lock (gate)
        {
            if (invalidated || TpmClockContinuity.Evaluate(previous, current) ==
                TpmClockContinuityObservation.ReanchorRequired)
            {
                invalidated = true;
                previous = null;
                return TpmClockContinuityObservation.ReanchorRequired;
            }

            previous = current;
            return TpmClockContinuityObservation.RawContinuityObserved;
        }
    }

    internal void Invalidate()
    {
        lock (gate)
        {
            invalidated = true;
            previous = null;
        }
    }
}
