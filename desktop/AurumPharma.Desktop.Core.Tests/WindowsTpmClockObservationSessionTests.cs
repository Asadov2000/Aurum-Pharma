using System.Security.Cryptography;
using AurumPharma.Desktop.Core.Security;

namespace AurumPharma.Desktop.Core.Tests;

public sealed class WindowsTpmClockObservationSessionTests
{
    [Fact]
    public void RegistrationPrecedesBaselineAndValidObservationsAdvanceTheBaseline()
    {
        var transport = new FakePowerTransport();
        var reads = 0;
        using var session = WindowsTpmClockObservationSession.Open(transport, () =>
        {
            Assert.Equal(1, transport.RegisterCalls);
            Assert.NotEqual(0, transport.CallbackContext);
            return ++reads switch
            {
                1 => Reading(100),
                2 => Reading(200),
                _ => Reading(150),
            };
        });

        Assert.Equal(TpmClockContinuityObservation.RawContinuityObserved, session.Observe());
        Assert.Equal(TpmClockContinuityObservation.ReanchorRequired, session.Observe());
        Assert.Equal(TpmClockContinuityObservation.ReanchorRequired, session.Observe());
        Assert.Equal(3, reads);
    }

    [Theory]
    [InlineData(0U)]
    [InlineData(4U)]
    [InlineData(7U)]
    [InlineData(18U)]
    [InlineData(uint.MaxValue)]
    public void EveryPowerNotificationPermanentlyDeniesFurtherHardwareReads(uint notification)
    {
        var transport = new FakePowerTransport();
        var reads = 0;
        using var session = WindowsTpmClockObservationSession.Open(transport, () =>
        {
            reads++;
            return Reading(100);
        });

        // The setting is deliberately not a valid pointer: callbacks must not dereference it.
        Assert.Equal(0U, transport.Fire(notification, -1));
        Assert.Equal(TpmClockContinuityObservation.ReanchorRequired, session.Observe());
        Assert.Equal(TpmClockContinuityObservation.ReanchorRequired, session.Observe());
        Assert.Equal(1, reads);
    }

    [Theory]
    [InlineData("registration")]
    [InlineData("baseline")]
    public void PowerNotificationDuringOpenRejectsBaselineAndCleansUp(string stage)
    {
        var transport = new FakePowerTransport { FireDuringRegister = stage == "registration" };
        var reads = 0;

        AssertSanitized(() => WindowsTpmClockObservationSession.Open(transport, () =>
        {
            reads++;
            if (stage == "baseline")
            {
                transport.Fire();
            }
            return Reading(100);
        }));

        Assert.Equal(1, transport.UnregisterCalls);
        Assert.Equal(stage == "baseline" ? 1 : 0, reads);
        Assert.Equal(0U, transport.Fire());
    }

    [Theory]
    [InlineData("status-with-handle")]
    [InlineData("status-without-handle")]
    [InlineData("zero-handle")]
    [InlineData("throw-with-handle")]
    [InlineData("throw-without-handle")]
    public void RegistrationFailureIsSanitizedAndCleansUpOnlyIssuedHandles(string failure)
    {
        var hasHandle = failure is "status-with-handle" or "throw-with-handle";
        var transport = new FakePowerTransport
        {
            Registration = hasHandle ? 42 : 0,
            RegisterStatus = failure.StartsWith("status", StringComparison.Ordinal) ? 5U : 0U,
            ThrowDuringRegister = failure.StartsWith("throw", StringComparison.Ordinal),
        };
        var reads = 0;

        AssertSanitized(() => WindowsTpmClockObservationSession.Open(transport, () =>
        {
            reads++;
            return Reading(100);
        }));

        Assert.Equal(0, reads);
        Assert.Equal(hasHandle ? 1 : 0, transport.UnregisterCalls);
        Assert.Equal(0U, transport.Fire());
    }

    [Theory]
    [InlineData("unsafe")]
    [InlineData("throw")]
    public void InvalidBaselineIsSanitizedAndRegistrationIsReleased(string failure)
    {
        var transport = new FakePowerTransport();

        AssertSanitized(() => WindowsTpmClockObservationSession.Open(transport, () =>
            failure == "throw" ? throw new InvalidOperationException("private native detail") : Reading(100, false)));

        Assert.Equal(1, transport.UnregisterCalls);
        Assert.Equal(0U, transport.Fire());
    }

    [Fact]
    public void FailedOpenRemainsSanitizedWhenCleanupAlsoThrows()
    {
        var transport = new FakePowerTransport { ThrowDuringUnregister = true };

        AssertSanitized(() => WindowsTpmClockObservationSession.Open(transport, () =>
            throw new InvalidOperationException("private read detail")));

        Assert.Equal(1, transport.UnregisterCalls);
        Assert.Equal(0U, transport.Fire());
    }

    [Fact]
    public void MissingTransportOrReaderFailsBeforeAccessingHardware()
    {
        var transport = new FakePowerTransport();
        var reads = 0;
        AssertSanitized(() => WindowsTpmClockObservationSession.Open(null!, () =>
        {
            reads++;
            return Reading(100);
        }));
        AssertSanitized(() => WindowsTpmClockObservationSession.Open(transport, null!));

        Assert.Equal(0, reads);
        Assert.Equal(0, transport.RegisterCalls);
    }

    [Theory]
    [InlineData("unsafe")]
    [InlineData("throw")]
    public void FailedObservationIsStickyAndDoesNotExposeReadExceptions(string failure)
    {
        var transport = new FakePowerTransport();
        var reads = 0;
        using var session = WindowsTpmClockObservationSession.Open(transport, () =>
        {
            if (++reads == 1)
            {
                return Reading(100);
            }
            return failure == "throw"
                ? throw new InvalidOperationException("private native detail")
                : Reading(101, false);
        });

        Assert.Equal(TpmClockContinuityObservation.ReanchorRequired, session.Observe());
        Assert.Equal(TpmClockContinuityObservation.ReanchorRequired, session.Observe());
        Assert.Equal(2, reads);
    }

    [Fact]
    public async Task CallbackDuringBlockedReadDeniesItsResultWithoutWaitingForObservationGate()
    {
        var transport = new FakePowerTransport();
        using var readStarted = new ManualResetEventSlim();
        using var releaseRead = new ManualResetEventSlim();
        var reads = 0;
        using var session = WindowsTpmClockObservationSession.Open(transport, () =>
        {
            if (Interlocked.Increment(ref reads) == 2)
            {
                readStarted.Set();
                Assert.True(releaseRead.Wait(TimeSpan.FromSeconds(5)));
            }
            return Reading(100);
        });

        var observation = Task.Run(session.Observe);
        try
        {
            Assert.True(readStarted.Wait(TimeSpan.FromSeconds(5)));
            Assert.Equal(0U, await Task.Run(() => transport.Fire()).WaitAsync(TimeSpan.FromSeconds(5)));
        }
        finally
        {
            releaseRead.Set();
        }

        Assert.Equal(TpmClockContinuityObservation.ReanchorRequired,
            await observation.WaitAsync(TimeSpan.FromSeconds(5)));
        Assert.Equal(TpmClockContinuityObservation.ReanchorRequired, session.Observe());
        Assert.Equal(2, reads);
    }

    [Fact]
    public void DisposeIsIdempotentAndLateCallbacksCannotReadHardware()
    {
        var transport = new FakePowerTransport();
        var reads = 0;
        var session = WindowsTpmClockObservationSession.Open(transport, () =>
        {
            reads++;
            return Reading(100);
        });

        session.Dispose();
        session.Dispose();

        Assert.Equal(1, transport.UnregisterCalls);
        Assert.Equal(0U, transport.Fire());
        Assert.Equal(TpmClockContinuityObservation.ReanchorRequired, session.Observe());
        Assert.Equal(1, reads);
    }

    [Fact]
    public async Task DisposeDuringBlockedObservationDeniesItsResultWithoutWaitingForRead()
    {
        var transport = new FakePowerTransport { FireDuringUnregister = true };
        using var readStarted = new ManualResetEventSlim();
        using var releaseRead = new ManualResetEventSlim();
        var reads = 0;
        using var session = WindowsTpmClockObservationSession.Open(transport, () =>
        {
            if (Interlocked.Increment(ref reads) == 2)
            {
                readStarted.Set();
                Assert.True(releaseRead.Wait(TimeSpan.FromSeconds(5)));
            }
            return Reading(100);
        });

        var observation = Task.Run(session.Observe);
        try
        {
            Assert.True(readStarted.Wait(TimeSpan.FromSeconds(5)));
            await Task.Run(session.Dispose).WaitAsync(TimeSpan.FromSeconds(5));
            Assert.Equal(1, transport.UnregisterCalls);
        }
        finally
        {
            releaseRead.Set();
        }

        Assert.Equal(TpmClockContinuityObservation.ReanchorRequired,
            await observation.WaitAsync(TimeSpan.FromSeconds(5)));
        Assert.Equal(TpmClockContinuityObservation.ReanchorRequired, session.Observe());
        Assert.Equal(2, reads);
    }

    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public void FailedUnregisterStillRemovesCallbackRouteAndDisposeDoesNotRetry(bool throws)
    {
        var transport = new FakePowerTransport
        {
            UnregisterStatus = throws ? 0U : 5U,
            ThrowDuringUnregister = throws,
        };
        var session = WindowsTpmClockObservationSession.Open(transport, () => Reading(100));

        AssertSanitized(session.Dispose);
        session.Dispose();

        Assert.Equal(1, transport.UnregisterCalls);
        Assert.Equal(0U, transport.Fire());
        Assert.Equal(TpmClockContinuityObservation.ReanchorRequired, session.Observe());
    }

    [Fact]
    public void CallbackFromUnregisterDoesNotDeadlockOrThrow()
    {
        var transport = new FakePowerTransport { FireDuringUnregister = true };
        var session = WindowsTpmClockObservationSession.Open(transport, () => Reading(100));

        session.Dispose();

        Assert.Equal(1, transport.UnregisterCalls);
        Assert.Equal(0U, transport.UnregisterCallbackResult);
    }

    [Fact]
    public void PowerCallbackIsRoutedOnlyToItsOwnSession()
    {
        var firstTransport = new FakePowerTransport();
        var secondTransport = new FakePowerTransport();
        using var first = WindowsTpmClockObservationSession.Open(firstTransport, () => Reading(100));
        using var second = WindowsTpmClockObservationSession.Open(secondTransport, () => Reading(100));

        Assert.NotEqual(firstTransport.CallbackContext, secondTransport.CallbackContext);
        Assert.Same(firstTransport.RegisteredCallback, secondTransport.RegisteredCallback);
        firstTransport.Fire();

        Assert.Equal(TpmClockContinuityObservation.ReanchorRequired, first.Observe());
        Assert.Equal(TpmClockContinuityObservation.RawContinuityObserved, second.Observe());
        first.Dispose();
        Assert.Equal(0U, firstTransport.Fire());
        Assert.Equal(TpmClockContinuityObservation.RawContinuityObserved, second.Observe());
    }

    [Theory]
    [InlineData("success")]
    [InlineData("status")]
    [InlineData("throw")]
    public void DisposedRegistrationRemovesItsRouteEvenWhenNativeUnregisterFails(string outcome)
    {
        var transport = new FakePowerTransport
        {
            UnregisterStatus = outcome == "status" ? 5U : 0U,
            ThrowDuringUnregister = outcome == "throw",
        };
        var callbacks = 0;
        using var registration = WindowsPowerContinuityRegistration.Open(transport, () => callbacks++);

        Assert.Equal(0U, transport.Fire());
        Assert.Equal(1, callbacks);
        if (outcome == "success")
        {
            registration.Dispose();
        }
        else
        {
            AssertSanitized(registration.Dispose);
        }

        Assert.Equal(0U, transport.Fire());
        Assert.Equal(0U, transport.Fire(uint.MaxValue, -1));
        Assert.Equal(1, callbacks);
        registration.Dispose();
        Assert.Equal(1, transport.UnregisterCalls);
    }

    [Fact]
    public void CallbackFailureReturnsNativeErrorWithoutAllowingManagedExceptionAcrossBoundary()
    {
        var transport = new FakePowerTransport();
        using var registration = WindowsPowerContinuityRegistration.Open(transport, () =>
            throw new InvalidOperationException("private callback detail"));

        Assert.Equal(31U, transport.Fire());
        registration.Dispose();
        Assert.Equal(0U, transport.Fire());
    }

    [Fact]
    public void LastOpaqueContextCanBeIssuedOnlyOnceAndExhaustionNeverWraps()
    {
        long counter = (long)nint.MaxValue - 1;

        Assert.Equal(nint.MaxValue, WindowsPowerContinuityRegistration.AllocateContext(ref counter));
        AssertSanitized(() => WindowsPowerContinuityRegistration.AllocateContext(ref counter));
        AssertSanitized(() => WindowsPowerContinuityRegistration.AllocateContext(ref counter));
        Assert.Equal((long)nint.MaxValue, counter);
    }

    [Fact]
    public void NegativeOpaqueContextCounterFailsWithoutMutation()
    {
        long counter = -1;

        AssertSanitized(() => WindowsPowerContinuityRegistration.AllocateContext(ref counter));

        Assert.Equal(-1L, counter);
    }

    [Fact]
    public void PublicEntryPointRejectsOtherOperatingSystemsWithoutHardwareAccess()
    {
        if (!OperatingSystem.IsWindows())
        {
            Assert.Throws<PlatformNotSupportedException>(() => WindowsTpmClockObservationSession.Open());
        }
    }

    private static TpmClockReading Reading(ulong value, bool safe = true) => new(value, value, 1, 2, safe);

    private static void AssertSanitized(Action action)
    {
        var error = Assert.Throws<CryptographicException>(action);
        Assert.Equal("Offline TPM observation is unavailable", error.Message);
        Assert.Null(error.InnerException);
    }

    private sealed class FakePowerTransport : IPowerNotificationTransport
    {
        private PowerNotificationCallback? callback;

        public nint Registration { get; init; } = 42;
        public PowerNotificationCallback? RegisteredCallback => callback;
        public nint CallbackContext { get; private set; }
        public uint RegisterStatus { get; init; }
        public uint UnregisterStatus { get; init; }
        public bool ThrowDuringRegister { get; init; }
        public bool ThrowDuringUnregister { get; init; }
        public bool FireDuringRegister { get; init; }
        public bool FireDuringUnregister { get; init; }
        public int RegisterCalls { get; private set; }
        public int UnregisterCalls { get; private set; }
        public uint UnregisterCallbackResult { get; private set; }

        public uint Register(PowerNotificationCallback notificationCallback, nint context, out nint registration)
        {
            RegisterCalls++;
            callback = notificationCallback;
            CallbackContext = context;
            registration = Registration;
            if (FireDuringRegister)
            {
                Fire();
            }
            if (ThrowDuringRegister)
            {
                throw new InvalidOperationException("private native detail");
            }
            return RegisterStatus;
        }

        public uint Unregister(nint registration)
        {
            UnregisterCalls++;
            Assert.Equal(Registration, registration);
            if (FireDuringUnregister)
            {
                // Model a native unregister waiting for an in-flight callback on another thread.
                UnregisterCallbackResult = Task.Run(() => Fire()).WaitAsync(TimeSpan.FromSeconds(5))
                    .GetAwaiter().GetResult();
            }
            if (ThrowDuringUnregister)
            {
                throw new InvalidOperationException("private native detail");
            }
            return UnregisterStatus;
        }

        public uint Fire(uint type = 4, nint setting = 0) => callback!(CallbackContext, type, setting);
    }
}
