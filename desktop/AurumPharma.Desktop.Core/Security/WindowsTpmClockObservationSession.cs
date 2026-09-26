namespace AurumPharma.Desktop.Core.Security;

/// <summary>
/// Read-only, ephemeral TPM observations guarded by Windows power notifications.
/// This never establishes UTC, durable continuity, identity or login admission.
/// A process restart creates a different observer, not trusted recovery.
/// The owner must dispose this session; otherwise its native registration and
/// callback route retain it until process exit. Use a using statement.
/// </summary>
public sealed class WindowsTpmClockObservationSession : IDisposable
{
    private readonly object gate = new();
    private readonly Func<TpmClockReading> readClock;
    private WindowsPowerContinuityRegistration? registration;
    private TpmClockContinuityMonitor? monitor;
    private int invalidated;
    private int disposed;

    private WindowsTpmClockObservationSession(Func<TpmClockReading> readClock)
    {
        this.readClock = readClock;
    }

    public static WindowsTpmClockObservationSession Open()
    {
        if (!OperatingSystem.IsWindows())
        {
            throw new PlatformNotSupportedException("TPM observation requires Windows.");
        }
        return Open(new NativePowerNotificationTransport(), WindowsTpmClockReader.Read);
    }

    internal static WindowsTpmClockObservationSession Open(
        IPowerNotificationTransport transport, Func<TpmClockReading> readClock)
    {
        if (readClock is null)
        {
            throw WindowsPowerContinuityRegistration.Failure();
        }

        var result = new WindowsTpmClockObservationSession(readClock);
        try
        {
            // Listen before taking a baseline; events during registration or the
            // first TPM read must not be erased by constructing a fresh monitor.
            result.registration = WindowsPowerContinuityRegistration.Open(transport, result.Invalidate);
            if (Volatile.Read(ref result.invalidated) != 0)
            {
                throw WindowsPowerContinuityRegistration.Failure();
            }
            var baseline = readClock();
            if (TpmClockContinuity.Evaluate(baseline, baseline) !=
                TpmClockContinuityObservation.RawContinuityObserved)
            {
                throw WindowsPowerContinuityRegistration.Failure();
            }
            result.monitor = new TpmClockContinuityMonitor(baseline);
            if (Volatile.Read(ref result.invalidated) != 0)
            {
                throw WindowsPowerContinuityRegistration.Failure();
            }
            return result;
        }
        catch (Exception)
        {
            try { result.Dispose(); }
            catch (Exception) { /* Preserve a sanitized failed-open result. */ }
            throw WindowsPowerContinuityRegistration.Failure();
        }
    }

    /// <summary>Returns only an advisory observation; failures latch until disposal.</summary>
    public TpmClockContinuityObservation Observe()
    {
        lock (gate)
        {
            if (Volatile.Read(ref invalidated) != 0 || monitor is null)
            {
                return TpmClockContinuityObservation.ReanchorRequired;
            }
            try
            {
                var observation = monitor.Observe(readClock());
                if (observation == TpmClockContinuityObservation.ReanchorRequired)
                {
                    Invalidate();
                }
                // A callback during the native read wins. Events after this final
                // check invalidate future reads; this result is only a historical
                // observation and cannot serve as a reusable authorization token.
                return Volatile.Read(ref invalidated) == 0
                    ? observation : TpmClockContinuityObservation.ReanchorRequired;
            }
            catch (Exception)
            {
                Invalidate();
                return TpmClockContinuityObservation.ReanchorRequired;
            }
        }
    }

    private void Invalidate() => Interlocked.Exchange(ref invalidated, 1);

    public void Dispose()
    {
        Invalidate();
        if (Interlocked.Exchange(ref disposed, 1) == 0)
        {
            // Never unregister under the observation lock or from a callback.
            registration?.Dispose();
        }
    }
}
