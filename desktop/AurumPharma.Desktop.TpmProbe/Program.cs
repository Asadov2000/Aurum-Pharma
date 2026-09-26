using System.Security.Cryptography;
using System.Text.Json;
using AurumPharma.Desktop.Core.Security;

// Developer-only, explicit read-only capability probe. No key names, raw clock
// values, TPM identifiers, account data or native diagnostics are printed.
try
{
    var first = WindowsTpmClockReader.Read();
    Thread.Sleep(100);
    var second = WindowsTpmClockReader.Read();
    var continuityObserved = TpmClockContinuity.Evaluate(first, second) ==
        TpmClockContinuityObservation.RawContinuityObserved;
    var counterProgressObserved = second.ClockMilliseconds > first.ClockMilliseconds &&
        second.TimeMilliseconds >= first.TimeMilliseconds;
    bool powerGuardContinuityObserved;
    using (var guarded = WindowsTpmClockObservationSession.Open())
    {
        Thread.Sleep(100);
        powerGuardContinuityObserved = guarded.Observe() ==
            TpmClockContinuityObservation.RawContinuityObserved;
    }
    // Disposal must finish before reporting success. A failed unregister cannot
    // emit an available record followed by a contradictory failure record.
    Console.WriteLine(JsonSerializer.Serialize(new
    {
        schemaVersion = 1,
        status = "read-only-available",
        safeOnBothReads = first.Safe && second.Safe,
        continuityObserved,
        counterProgressObserved,
        powerGuardContinuityObserved,
        runtimeAdmission = false,
        hardwareAcceptance = false
    }));
    return 0;
}
catch (Exception error) when (error is CryptographicException or PlatformNotSupportedException)
{
    Console.WriteLine(JsonSerializer.Serialize(new
    {
        schemaVersion = 1,
        status = "read-only-unavailable",
        runtimeAdmission = false,
        hardwareAcceptance = false
    }));
    return 2;
}
