using System.Globalization;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace AurumPharma.Desktop.Core.Security;

/// <summary>Immutable snapshot data. String formatting never includes state or identifiers.</summary>
public abstract record ProtectedCashierStateValue
{
    public sealed override string ToString() => GetType().Name;
}

/// <summary>The trusted operator supplies the expected workplace independently of snapshot data.</summary>
public sealed record ProtectedCashierWorkplaceScope : ProtectedCashierStateValue
{
    public ProtectedCashierWorkplaceScope(
        Guid activationId, Guid tenantId, Guid branchId, Guid edgeNodeId, Guid registerId, long writerEpoch)
    {
        foreach (var id in new[] { activationId, tenantId, branchId, edgeNodeId, registerId })
        {
            if (!ProtectedCashierSnapshotParser.IsUuid4(id)) { throw ProtectedCashierSnapshotParser.Failure(); }
        }
        if (writerEpoch < 1) { throw ProtectedCashierSnapshotParser.Failure(); }
        ActivationId = activationId;
        TenantId = tenantId;
        BranchId = branchId;
        EdgeNodeId = edgeNodeId;
        RegisterId = registerId;
        WriterEpoch = writerEpoch;
    }
    public Guid ActivationId { get; }
    public Guid TenantId { get; }
    public Guid BranchId { get; }
    public Guid EdgeNodeId { get; }
    public Guid RegisterId { get; }
    public long WriterEpoch { get; }
    public string Capability => "cash_sale_v1";
}

public sealed record ProtectedCashierUserScope : ProtectedCashierStateValue
{
    internal ProtectedCashierUserScope(ProtectedCashierWorkplaceScope workplace, Guid userId)
    {
        Workplace = workplace;
        UserId = userId;
    }
    public ProtectedCashierWorkplaceScope Workplace { get; }
    public Guid UserId { get; }
    public Guid ActivationId => Workplace.ActivationId;
    public Guid TenantId => Workplace.TenantId;
    public Guid BranchId => Workplace.BranchId;
    public Guid EdgeNodeId => Workplace.EdgeNodeId;
    public Guid RegisterId => Workplace.RegisterId;
    public long WriterEpoch => Workplace.WriterEpoch;
    public string Capability => Workplace.Capability;
}

/// <summary>Expected key identity comes from independently authenticated device enrollment.</summary>
public sealed record ProtectedCashierDeviceBinding : ProtectedCashierStateValue
{
    public ProtectedCashierDeviceBinding(Guid keyId, string spkiSha256)
    {
        if (!ProtectedCashierSnapshotParser.IsUuid4(keyId) ||
            !ProtectedCashierSnapshotParser.IsDigest(spkiSha256)) { throw ProtectedCashierSnapshotParser.Failure(); }
        KeyId = keyId;
        SpkiSha256 = spkiSha256;
    }
    public string Method => "tpm2";
    public Guid KeyId { get; }
    public string SpkiSha256 { get; }
}

public sealed record ProtectedCashierReference : ProtectedCashierStateValue
{
    internal ProtectedCashierReference(Guid referenceId, long revision)
    {
        ReferenceId = referenceId;
        Revision = revision;
    }
    public Guid ReferenceId { get; }
    public long Revision { get; }
}

public sealed record ProtectedCashierTenantPolicy : ProtectedCashierStateValue
{
    internal ProtectedCashierTenantPolicy(Guid tenantId, long version, string tenantStatus,
        string mode, string[] blockedModules, DateTimeOffset? expiresAt)
    {
        TenantId = tenantId;
        Version = version;
        TenantStatus = tenantStatus;
        Mode = mode;
        BlockedModules = Array.AsReadOnly(blockedModules.ToArray());
        ExpiresAt = expiresAt;
    }
    public Guid TenantId { get; }
    public long Version { get; }
    public string TenantStatus { get; }
    public string Mode { get; }
    public IReadOnlyList<string> BlockedModules { get; }
    public DateTimeOffset? ExpiresAt { get; }
}

public sealed record ProtectedCashierPolicyFloor : ProtectedCashierStateValue
{
    internal ProtectedCashierPolicyFloor(long deliverySequence, string claimsHash, ProtectedCashierTenantPolicy policy)
    {
        DeliverySequence = deliverySequence;
        ClaimsHash = claimsHash;
        Policy = policy;
    }
    public long DeliverySequence { get; }
    public string ClaimsHash { get; }
    public ProtectedCashierTenantPolicy Policy { get; }
}

/// <summary>A serialized observation; parsing does not establish current or trusted UTC.</summary>
public sealed record ProtectedCashierObservedTime : ProtectedCashierStateValue
{
    internal ProtectedCashierObservedTime(DateTimeOffset utcNow, long monotonicCounter, string continuityId)
    {
        UtcNow = utcNow;
        MonotonicCounter = monotonicCounter;
        ContinuityId = continuityId;
    }
    public DateTimeOffset UtcNow { get; }
    public long MonotonicCounter { get; }
    public string ContinuityId { get; }
}

public sealed record ProtectedCashierReauthenticationPolicy : ProtectedCashierStateValue
{
    internal ProtectedCashierReauthenticationPolicy(ProtectedCashierReference reference, long maxSessionSeconds,
        string[] confirmationCommands, long maxConfirmationSeconds)
    {
        Reference = reference;
        MaxSessionSeconds = maxSessionSeconds;
        ConfirmationCommands = Array.AsReadOnly(confirmationCommands.ToArray());
        MaxConfirmationSeconds = maxConfirmationSeconds;
    }
    public ProtectedCashierReference Reference { get; }
    public long MaxSessionSeconds { get; }
    public IReadOnlyList<string> ConfirmationCommands { get; }
    public long MaxConfirmationSeconds { get; }
}

public sealed record ProtectedCashierAuthorization : ProtectedCashierStateValue
{
    internal ProtectedCashierAuthorization(Guid leaseId, ProtectedCashierUserScope scope,
        ProtectedCashierDeviceBinding deviceBinding, long policyRevision, long tenantAccessRevision,
        long subjectRevision, string[] allowedCommands, ProtectedCashierReference admission,
        ProtectedCashierReference localCredential, ProtectedCashierReauthenticationPolicy reauthenticationPolicy,
        Guid shiftContextId, string status)
    {
        LeaseId = leaseId;
        Scope = scope;
        DeviceBinding = deviceBinding;
        PolicyRevision = policyRevision;
        TenantAccessRevision = tenantAccessRevision;
        SubjectRevision = subjectRevision;
        AllowedCommands = Array.AsReadOnly(allowedCommands.ToArray());
        Admission = admission;
        LocalCredential = localCredential;
        ReauthenticationPolicy = reauthenticationPolicy;
        ShiftContextId = shiftContextId;
        Status = status;
    }
    public Guid LeaseId { get; }
    public ProtectedCashierUserScope Scope { get; }
    public ProtectedCashierDeviceBinding DeviceBinding { get; }
    public long PolicyRevision { get; }
    public long TenantAccessRevision { get; }
    public long SubjectRevision { get; }
    public IReadOnlyList<string> AllowedCommands { get; }
    public ProtectedCashierReference Admission { get; }
    public ProtectedCashierReference LocalCredential { get; }
    public string CredentialAudience => "aurum-edge-local-auth-v1";
    public ProtectedCashierReauthenticationPolicy ReauthenticationPolicy { get; }
    public Guid ShiftContextId { get; }
    public string Status { get; }
}

public sealed record ProtectedCashierCredentialContext : ProtectedCashierStateValue
{
    internal ProtectedCashierCredentialContext(ProtectedCashierUserScope scope,
        ProtectedCashierReference localCredential, ProtectedCashierDeviceBinding deviceBinding,
        ProtectedCashierDeviceBinding protectionKey)
    {
        Scope = scope;
        LocalCredential = localCredential;
        DeviceBinding = deviceBinding;
        ProtectionKey = protectionKey;
    }
    public long SchemaVersion => 1;
    public string Audience => "aurum-edge-local-auth-v1";
    public ProtectedCashierUserScope Scope { get; }
    public ProtectedCashierReference LocalCredential { get; }
    public ProtectedCashierDeviceBinding DeviceBinding { get; }
    public ProtectedCashierDeviceBinding ProtectionKey { get; }
    public byte[] ContextDigestSha256 => ProtectedCashierSnapshotParser.CredentialContextDigest(this);
}

public sealed record ProtectedCashierGuard : ProtectedCashierStateValue
{
    internal ProtectedCashierGuard(Guid tenantId, Guid branchId, Guid edgeNodeId, Guid userId,
        Guid credentialId, long credentialRevision, string contextDigest, long failedAttempts,
        long attemptSequence, DateTimeOffset? blockedUntil, DateTimeOffset lastUtc,
        long lastCounter, string continuityId)
    {
        TenantId = tenantId;
        BranchId = branchId;
        EdgeNodeId = edgeNodeId;
        UserId = userId;
        CredentialId = credentialId;
        CredentialRevision = credentialRevision;
        ContextDigest = contextDigest;
        FailedAttempts = failedAttempts;
        AttemptSequence = attemptSequence;
        BlockedUntil = blockedUntil;
        LastUtc = lastUtc;
        LastCounter = lastCounter;
        ContinuityId = continuityId;
    }
    public Guid TenantId { get; }
    public Guid BranchId { get; }
    public Guid EdgeNodeId { get; }
    public Guid UserId { get; }
    public Guid CredentialId { get; }
    public long CredentialRevision { get; }
    public string ContextDigest { get; }
    public long FailedAttempts { get; }
    public long AttemptSequence { get; }
    public DateTimeOffset? BlockedUntil { get; }
    public DateTimeOffset LastUtc { get; }
    public long LastCounter { get; }
    public string ContinuityId { get; }
}

public sealed record ProtectedCashierRecord : ProtectedCashierStateValue
{
    internal ProtectedCashierRecord(ProtectedCashierAuthorization authorization,
        ProtectedCashierCredentialContext credentialContext, ProtectedCashierGuard guard)
    {
        Authorization = authorization;
        CredentialContext = credentialContext;
        Guard = guard;
    }
    public ProtectedCashierAuthorization Authorization { get; }
    public ProtectedCashierCredentialContext CredentialContext { get; }
    public ProtectedCashierGuard Guard { get; }
}

public sealed record ProtectedCashierConfirmation : ProtectedCashierStateValue
{
    internal ProtectedCashierConfirmation(string command, DateTimeOffset confirmedAt, long monotonicCounter)
    {
        Command = command;
        ConfirmedAt = confirmedAt;
        MonotonicCounter = monotonicCounter;
    }
    public string Command { get; }
    public DateTimeOffset ConfirmedAt { get; }
    public long MonotonicCounter { get; }
}

public sealed record ProtectedCashierSession : ProtectedCashierStateValue
{
    internal ProtectedCashierSession(Guid sessionId, Guid leaseId, ProtectedCashierUserScope scope,
        ProtectedCashierReference localCredential, ProtectedCashierReference reauthenticationPolicy,
        Guid shiftContextId, DateTimeOffset authenticatedAt, DateTimeOffset expiresAt,
        string continuityId, long monotonicCounter, ProtectedCashierConfirmation? confirmation)
    {
        SessionId = sessionId;
        LeaseId = leaseId;
        Scope = scope;
        LocalCredential = localCredential;
        ReauthenticationPolicy = reauthenticationPolicy;
        ShiftContextId = shiftContextId;
        AuthenticatedAt = authenticatedAt;
        ExpiresAt = expiresAt;
        ContinuityId = continuityId;
        MonotonicCounter = monotonicCounter;
        Confirmation = confirmation;
    }
    public Guid SessionId { get; }
    public Guid LeaseId { get; }
    public ProtectedCashierUserScope Scope { get; }
    public ProtectedCashierReference LocalCredential { get; }
    public string CredentialAudience => "aurum-edge-local-auth-v1";
    public ProtectedCashierReference ReauthenticationPolicy { get; }
    public Guid ShiftContextId { get; }
    public DateTimeOffset AuthenticatedAt { get; }
    public DateTimeOffset ExpiresAt { get; }
    public string ContinuityId { get; }
    public long MonotonicCounter { get; }
    public ProtectedCashierConfirmation? Confirmation { get; }
}

/// <summary>
/// Structurally validated untrusted data. Parsing is not enrollment, signature verification,
/// clock acceptance, checkpoint verification or a decision to admit a cashier.
/// </summary>
public sealed class ProtectedCashierStateSnapshot
{
    public const int MaximumPayloadBytes = 1_048_576;
    private readonly byte[] checkpointContextSha256;
    private readonly byte[] payloadDigest;

    internal ProtectedCashierStateSnapshot(ProtectedCashierWorkplaceScope scope,
        ProtectedCashierDeviceBinding deviceBinding, byte[] checkpointContextSha256,
        ProtectedCashierPolicyFloor policyFloor, ProtectedCashierObservedTime observedTime,
        ProtectedCashierRecord[] cashiers, ProtectedCashierSession? workplaceSession, byte[] payloadDigest)
    {
        Scope = scope;
        DeviceBinding = deviceBinding;
        this.checkpointContextSha256 = checkpointContextSha256.ToArray();
        PolicyFloor = policyFloor;
        ObservedTime = observedTime;
        Cashiers = Array.AsReadOnly(cashiers.ToArray());
        WorkplaceSession = workplaceSession;
        this.payloadDigest = payloadDigest.ToArray();
    }
    public long SchemaVersion => 1;
    public ProtectedCashierWorkplaceScope Scope { get; }
    public ProtectedCashierDeviceBinding DeviceBinding { get; }
    public byte[] CheckpointContextSha256 => checkpointContextSha256.ToArray();
    public ProtectedCashierPolicyFloor PolicyFloor { get; }
    public ProtectedCashierObservedTime ObservedTime { get; }
    public IReadOnlyList<ProtectedCashierRecord> Cashiers { get; }
    public ProtectedCashierSession? WorkplaceSession { get; }
    public byte[] PayloadDigest => payloadDigest.ToArray();

    public static ProtectedCashierStateSnapshot Parse(ReadOnlySpan<byte> raw)
    {
        if (raw.Length is < 1 or > MaximumPayloadBytes) { throw ProtectedCashierSnapshotParser.Failure(); }
        var bytes = raw.ToArray();
        try { return ProtectedCashierSnapshotParser.Parse(bytes); }
        catch (Exception) { throw ProtectedCashierSnapshotParser.Failure(); }
        finally { CryptographicOperations.ZeroMemory(bytes); }
    }

    public void AssertBinding(ProtectedCashierWorkplaceScope expectedScope,
        ProtectedCashierDeviceBinding expectedDevice, ReadOnlySpan<byte> expectedCheckpointContext)
    {
        if (expectedScope is null || expectedDevice is null || Scope != expectedScope ||
            DeviceBinding != expectedDevice || expectedCheckpointContext.Length != 32 ||
            !CryptographicOperations.FixedTimeEquals(checkpointContextSha256, expectedCheckpointContext))
        {
            throw ProtectedCashierSnapshotParser.Failure();
        }
    }

    public override string ToString() => nameof(ProtectedCashierStateSnapshot);
}

internal static class ProtectedCashierSnapshotParser
{
    private const string Audience = "aurum-edge-local-auth-v1";
    private static ReadOnlySpan<byte> DigestDomain => "aurum:protected-cashier-state:v1\0"u8;
    private static ReadOnlySpan<byte> ContextDomain => "aurum:edge-local-credential-context:v1\0"u8;
    private static readonly string[] Commands =
        ["operation.result.read", "receipt.print", "receipt.reprint", "sale.cash.complete", "shift.close", "shift.open"];
    private static readonly string[] Modules = ["catalog", "incoming", "inventory", "suppliers", "pos", "staff", "settings"];
    private static readonly Regex UtcPattern = new(
        "\\A[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\\.[0-9]{1,6})?(?:Z|\\+00:00)\\z",
        RegexOptions.CultureInvariant | RegexOptions.NonBacktracking);

    internal static ProtectedCashierStateSnapshot Parse(byte[] bytes)
    {
        using var document = JsonDocument.Parse(bytes, new JsonDocumentOptions
        {
            AllowDuplicateProperties = false,
            AllowTrailingCommas = false,
            CommentHandling = JsonCommentHandling.Disallow,
            MaxDepth = 32,
        });
        var root = document.RootElement;
        ValidateAtoms(root);
        Object(root, "schema_version", "scope", "device_binding", "checkpoint_context_sha256",
            "policy_floor", "observed_time", "cashiers", "workplace_session");
        Integer(root.GetProperty("schema_version"), 1, 1);
        var scope = Workplace(root.GetProperty("scope"));
        var device = Device(root.GetProperty("device_binding"));
        var context = Convert.FromHexString(Digest(root, "checkpoint_context_sha256"));
        var floor = PolicyFloor(root.GetProperty("policy_floor"));
        var observed = Observed(root.GetProperty("observed_time"));
        var cashierArray = root.GetProperty("cashiers");
        if (cashierArray.ValueKind != JsonValueKind.Array || cashierArray.GetArrayLength() > 256) { throw Failure(); }
        var records = cashierArray.EnumerateArray().Select(Cashier).ToArray();
        var sessionValue = root.GetProperty("workplace_session");
        var session = sessionValue.ValueKind == JsonValueKind.Null ? null : Session(sessionValue);
        var snapshot = new ProtectedCashierStateSnapshot(scope, device, context, floor, observed,
            records, session, Hash(DigestDomain, bytes));
        ValidateBindings(snapshot);
        return snapshot;
    }

    private static ProtectedCashierWorkplaceScope Workplace(JsonElement value)
    {
        Object(value, "activation_id", "tenant_id", "branch_id", "edge_node_id", "register_id", "writer_epoch", "capability");
        return WorkplaceFields(value);
    }

    private static ProtectedCashierWorkplaceScope WorkplaceFields(JsonElement value)
    {
        Literal(value, "capability", "cash_sale_v1");
        return new ProtectedCashierWorkplaceScope(Uuid(value, "activation_id"), Uuid(value, "tenant_id"),
            Uuid(value, "branch_id"), Uuid(value, "edge_node_id"), Uuid(value, "register_id"), Number(value, "writer_epoch", 1));
    }

    private static ProtectedCashierUserScope UserScope(JsonElement value)
    {
        Object(value, "activation_id", "tenant_id", "branch_id", "edge_node_id", "register_id", "writer_epoch", "capability", "user_id");
        return new ProtectedCashierUserScope(WorkplaceFields(value), Uuid(value, "user_id"));
    }

    private static ProtectedCashierDeviceBinding Device(JsonElement value)
    {
        Object(value, "method", "key_id", "spki_sha256");
        Literal(value, "method", "tpm2");
        return new ProtectedCashierDeviceBinding(Uuid(value, "key_id"), Digest(value, "spki_sha256"));
    }

    private static ProtectedCashierReference Reference(JsonElement value)
    {
        Object(value, "reference_id", "revision");
        return new ProtectedCashierReference(Uuid(value, "reference_id"), Number(value, "revision", 1));
    }

    private static ProtectedCashierPolicyFloor PolicyFloor(JsonElement value)
    {
        Object(value, "delivery_sequence", "claims_hash", "policy");
        var policy = value.GetProperty("policy");
        Object(policy, "tenant_id", "version", "tenant_status", "mode", "blocked_modules", "expires_at");
        var mode = Literal(policy, "mode", "full", "readonly");
        var status = Literal(policy, "tenant_status", "setup", "trial", "active", "grace_period", "readonly", "suspended", "archived");
        var parsed = new ProtectedCashierTenantPolicy(Uuid(policy, "tenant_id"), Number(policy, "version", 1),
            status, mode, SortedStrings(policy.GetProperty("blocked_modules"), Modules, 0, 7), NullableTime(policy, "expires_at"));
        return new ProtectedCashierPolicyFloor(Number(value, "delivery_sequence", 1), Digest(value, "claims_hash"), parsed);
    }

    private static ProtectedCashierObservedTime Observed(JsonElement value)
    {
        Object(value, "utc_now", "monotonic_counter", "continuity_id");
        return new ProtectedCashierObservedTime(Time(value, "utc_now"), Number(value, "monotonic_counter"), Continuity(value));
    }

    private static ProtectedCashierReauthenticationPolicy Reauthentication(JsonElement value)
    {
        Object(value, "reference", "max_session_seconds", "confirmation_commands", "max_confirmation_seconds");
        return new ProtectedCashierReauthenticationPolicy(Reference(value.GetProperty("reference")),
            Number(value, "max_session_seconds", 1, 86_400),
            SortedStrings(value.GetProperty("confirmation_commands"), Commands, 0, 6),
            Number(value, "max_confirmation_seconds", 1, 300));
    }

    private static ProtectedCashierAuthorization Authorization(JsonElement value)
    {
        Object(value, "lease_id", "scope", "device_binding", "policy_revision", "tenant_access_revision",
            "subject_revision", "allowed_commands", "admission", "local_credential", "credential_audience",
            "reauthentication_policy", "shift_context_id", "status");
        Literal(value, "credential_audience", Audience);
        return new ProtectedCashierAuthorization(Uuid(value, "lease_id"), UserScope(value.GetProperty("scope")),
            Device(value.GetProperty("device_binding")), Number(value, "policy_revision", 1),
            Number(value, "tenant_access_revision", 1), Number(value, "subject_revision", 1),
            SortedStrings(value.GetProperty("allowed_commands"), Commands, 1, 6), Reference(value.GetProperty("admission")),
            Reference(value.GetProperty("local_credential")), Reauthentication(value.GetProperty("reauthentication_policy")),
            Uuid(value, "shift_context_id"), Literal(value, "status", "active", "revoked", "locally-blocked"));
    }

    private static ProtectedCashierCredentialContext CredentialContext(JsonElement value)
    {
        Object(value, "schema_version", "audience", "scope", "local_credential", "device_binding", "protection_key");
        Integer(value.GetProperty("schema_version"), 1, 1);
        Literal(value, "audience", Audience);
        var device = Device(value.GetProperty("device_binding"));
        var protector = Device(value.GetProperty("protection_key"));
        if (device.KeyId == protector.KeyId || device.SpkiSha256 == protector.SpkiSha256) { throw Failure(); }
        return new ProtectedCashierCredentialContext(UserScope(value.GetProperty("scope")),
            Reference(value.GetProperty("local_credential")), device, protector);
    }

    private static ProtectedCashierGuard Guard(JsonElement value)
    {
        Object(value, "tenant_id", "branch_id", "edge_node_id", "user_id", "credential_id", "credential_revision",
            "context_digest", "failed_attempts", "attempt_sequence", "blocked_until", "last_utc", "last_counter", "continuity_id");
        return new ProtectedCashierGuard(Uuid(value, "tenant_id"), Uuid(value, "branch_id"), Uuid(value, "edge_node_id"),
            Uuid(value, "user_id"), Uuid(value, "credential_id"), Number(value, "credential_revision", 1),
            Digest(value, "context_digest"), Number(value, "failed_attempts", 0, int.MaxValue), Number(value, "attempt_sequence"),
            NullableTime(value, "blocked_until"), Time(value, "last_utc"), Number(value, "last_counter"), Continuity(value));
    }

    private static ProtectedCashierRecord Cashier(JsonElement value)
    {
        Object(value, "authorization", "credential_context", "guard");
        return new ProtectedCashierRecord(Authorization(value.GetProperty("authorization")),
            CredentialContext(value.GetProperty("credential_context")), Guard(value.GetProperty("guard")));
    }

    private static ProtectedCashierSession Session(JsonElement value)
    {
        Object(value, "session_id", "lease_id", "scope", "local_credential", "credential_audience", "reauthentication_policy",
            "shift_context_id", "authenticated_at", "expires_at", "continuity_id", "monotonic_counter", "confirmation");
        Literal(value, "credential_audience", Audience);
        var authenticated = Time(value, "authenticated_at");
        var expires = Time(value, "expires_at");
        if (authenticated >= expires) { throw Failure(); }
        var confirmationValue = value.GetProperty("confirmation");
        ProtectedCashierConfirmation? confirmation = null;
        if (confirmationValue.ValueKind != JsonValueKind.Null)
        {
            Object(confirmationValue, "command", "confirmed_at", "monotonic_counter");
            confirmation = new ProtectedCashierConfirmation(Literal(confirmationValue, "command", Commands),
                Time(confirmationValue, "confirmed_at"), Number(confirmationValue, "monotonic_counter"));
        }
        return new ProtectedCashierSession(Uuid(value, "session_id"), Uuid(value, "lease_id"), UserScope(value.GetProperty("scope")),
            Reference(value.GetProperty("local_credential")), Reference(value.GetProperty("reauthentication_policy")),
            Uuid(value, "shift_context_id"), authenticated, expires, Continuity(value), Number(value, "monotonic_counter"), confirmation);
    }

    private static void ValidateBindings(ProtectedCashierStateSnapshot value)
    {
        if (value.PolicyFloor.Policy.TenantId != value.Scope.TenantId) { throw Failure(); }
        string? previousUser = null;
        foreach (var record in value.Cashiers)
        {
            var auth = record.Authorization;
            var context = record.CredentialContext;
            var guard = record.Guard;
            var user = auth.Scope.UserId.ToString("N");
            if ((previousUser is not null && string.CompareOrdinal(previousUser, user) >= 0) ||
                auth.Scope.Workplace != value.Scope || auth.DeviceBinding != value.DeviceBinding ||
                auth.TenantAccessRevision > value.PolicyFloor.Policy.Version || context.Scope != auth.Scope ||
                context.LocalCredential != auth.LocalCredential || context.DeviceBinding != auth.DeviceBinding ||
                guard.TenantId != auth.Scope.TenantId || guard.BranchId != auth.Scope.BranchId ||
                guard.EdgeNodeId != auth.Scope.EdgeNodeId || guard.UserId != auth.Scope.UserId ||
                guard.CredentialId != auth.LocalCredential.ReferenceId || guard.CredentialRevision != auth.LocalCredential.Revision ||
                guard.ContinuityId != value.ObservedTime.ContinuityId || guard.LastUtc > value.ObservedTime.UtcNow ||
                guard.LastCounter > value.ObservedTime.MonotonicCounter)
            {
                throw Failure();
            }
            previousUser = user;
            var digest = CredentialContextDigest(context);
            try
            {
                if (!CryptographicOperations.FixedTimeEquals(digest, Convert.FromHexString(guard.ContextDigest))) { throw Failure(); }
            }
            finally { CryptographicOperations.ZeroMemory(digest); }
        }
        var session = value.WorkplaceSession;
        if (session is null) { return; }
        var sessionRecord = value.Cashiers.SingleOrDefault(record => record.Authorization.Scope == session.Scope);
        if (sessionRecord is null) { throw Failure(); }
        var authorization = sessionRecord.Authorization;
        if (session.LeaseId != authorization.LeaseId || session.LocalCredential != authorization.LocalCredential ||
            session.ReauthenticationPolicy != authorization.ReauthenticationPolicy.Reference ||
            session.ShiftContextId != authorization.ShiftContextId || session.AuthenticatedAt > value.ObservedTime.UtcNow ||
            session.ContinuityId != value.ObservedTime.ContinuityId || session.MonotonicCounter > value.ObservedTime.MonotonicCounter)
        {
            throw Failure();
        }
        var confirmation = session.Confirmation;
        if (confirmation is not null && (confirmation.ConfirmedAt < session.AuthenticatedAt ||
            confirmation.ConfirmedAt > value.ObservedTime.UtcNow || confirmation.MonotonicCounter < session.MonotonicCounter ||
            confirmation.MonotonicCounter > value.ObservedTime.MonotonicCounter))
        {
            throw Failure();
        }
    }

    internal static byte[] CredentialContextDigest(ProtectedCashierCredentialContext value)
    {
        using var stream = new MemoryStream();
        using (var writer = new Utf8JsonWriter(stream))
        {
            // These values are fixed-format ASCII. Key order reproduces Python sort_keys=True.
            writer.WriteStartObject();
            writer.WriteString("audience", value.Audience);
            writer.WritePropertyName("device_binding"); WriteDevice(writer, value.DeviceBinding);
            writer.WritePropertyName("local_credential"); WriteReference(writer, value.LocalCredential);
            writer.WritePropertyName("protection_key"); WriteDevice(writer, value.ProtectionKey);
            writer.WriteNumber("schema_version", 1);
            writer.WritePropertyName("scope"); WriteScope(writer, value.Scope);
            writer.WriteEndObject();
        }
        var bytes = stream.ToArray();
        try { return Hash(ContextDomain, bytes); }
        finally { CryptographicOperations.ZeroMemory(bytes); }
    }

    private static void WriteDevice(Utf8JsonWriter writer, ProtectedCashierDeviceBinding value)
    {
        writer.WriteStartObject();
        writer.WriteString("key_id", value.KeyId);
        writer.WriteString("method", value.Method);
        writer.WriteString("spki_sha256", value.SpkiSha256);
        writer.WriteEndObject();
    }

    private static void WriteReference(Utf8JsonWriter writer, ProtectedCashierReference value)
    {
        writer.WriteStartObject();
        writer.WriteString("reference_id", value.ReferenceId);
        writer.WriteNumber("revision", value.Revision);
        writer.WriteEndObject();
    }

    private static void WriteScope(Utf8JsonWriter writer, ProtectedCashierUserScope value)
    {
        writer.WriteStartObject();
        writer.WriteString("activation_id", value.ActivationId);
        writer.WriteString("branch_id", value.BranchId);
        writer.WriteString("capability", value.Capability);
        writer.WriteString("edge_node_id", value.EdgeNodeId);
        writer.WriteString("register_id", value.RegisterId);
        writer.WriteString("tenant_id", value.TenantId);
        writer.WriteString("user_id", value.UserId);
        writer.WriteNumber("writer_epoch", value.WriterEpoch);
        writer.WriteEndObject();
    }

    private static byte[] Hash(ReadOnlySpan<byte> domain, ReadOnlySpan<byte> value)
    {
        using var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        hash.AppendData(domain);
        hash.AppendData(value);
        return hash.GetHashAndReset();
    }

    private static void Object(JsonElement value, params string[] names)
    {
        if (value.ValueKind != JsonValueKind.Object) { throw Failure(); }
        var seen = new HashSet<string>(StringComparer.Ordinal);
        foreach (var property in value.EnumerateObject())
        {
            if (!names.Contains(property.Name, StringComparer.Ordinal) || !seen.Add(property.Name)) { throw Failure(); }
        }
        if (seen.Count != names.Length) { throw Failure(); }
    }

    private static long Number(JsonElement parent, string name, long minimum = 0, long maximum = long.MaxValue)
        => Integer(parent.GetProperty(name), minimum, maximum);

    private static long Integer(JsonElement value, long minimum, long maximum)
    {
        if (value.ValueKind != JsonValueKind.Number || !value.TryGetInt64(out var number) || number < minimum || number > maximum)
        {
            throw Failure();
        }
        return number;
    }

    private static string Text(JsonElement parent, string name)
    {
        var value = parent.GetProperty(name);
        if (value.ValueKind != JsonValueKind.String) { throw Failure(); }
        return value.GetString() ?? throw Failure();
    }

    private static string Literal(JsonElement parent, string name, params string[] allowed)
    {
        var text = Text(parent, name);
        if (!allowed.Contains(text, StringComparer.Ordinal)) { throw Failure(); }
        return text;
    }

    private static Guid Uuid(JsonElement parent, string name)
    {
        var text = Text(parent, name);
        if (text.Length != 36 || !Guid.TryParseExact(text, "D", out var id) || !IsUuid4(id)) { throw Failure(); }
        return id;
    }

    internal static bool IsUuid4(Guid value)
    {
        var text = value.ToString("D");
        return text[14] == '4' && text[19] is '8' or '9' or 'a' or 'b';
    }

    private static string Digest(JsonElement parent, string name)
    {
        var text = Text(parent, name);
        if (!IsDigest(text)) { throw Failure(); }
        return text;
    }

    internal static bool IsDigest(string? value)
        => value is not null && value.Length == 64 && value.All(character => character is >= '0' and <= '9' or >= 'a' and <= 'f');

    private static DateTimeOffset Time(JsonElement parent, string name)
    {
        var text = Text(parent, name);
        if (!UtcPattern.IsMatch(text) ||
            int.Parse(text.AsSpan(11, 2), CultureInfo.InvariantCulture) > 23 ||
            int.Parse(text.AsSpan(14, 2), CultureInfo.InvariantCulture) > 59 ||
            int.Parse(text.AsSpan(17, 2), CultureInfo.InvariantCulture) > 59 ||
            !DateTimeOffset.TryParse(text, CultureInfo.InvariantCulture,
            DateTimeStyles.None, out var time) || time.Offset != TimeSpan.Zero) { throw Failure(); }
        return time;
    }

    private static DateTimeOffset? NullableTime(JsonElement parent, string name)
        => parent.GetProperty(name).ValueKind == JsonValueKind.Null ? null : Time(parent, name);

    private static string Continuity(JsonElement parent)
    {
        var text = Text(parent, "continuity_id");
        var length = ValidateUnicode(text);
        if (length is < 1 or > 128) { throw Failure(); }
        return text;
    }

    private static string[] SortedStrings(JsonElement value, string[] allowed, int minimum, int maximum)
    {
        if (value.ValueKind != JsonValueKind.Array || value.GetArrayLength() < minimum || value.GetArrayLength() > maximum) { throw Failure(); }
        var result = new List<string>();
        string? previous = null;
        foreach (var item in value.EnumerateArray())
        {
            if (item.ValueKind != JsonValueKind.String) { throw Failure(); }
            var text = item.GetString() ?? throw Failure();
            if (!allowed.Contains(text, StringComparer.Ordinal) || (previous is not null && string.CompareOrdinal(previous, text) >= 0)) { throw Failure(); }
            result.Add(text);
            previous = text;
        }
        return result.ToArray();
    }

    private static void ValidateAtoms(JsonElement value)
    {
        switch (value.ValueKind)
        {
            case JsonValueKind.Object:
                var seen = new HashSet<string>(StringComparer.Ordinal);
                foreach (var property in value.EnumerateObject())
                {
                    ValidateUnicode(property.Name);
                    if (!seen.Add(property.Name)) { throw Failure(); }
                    ValidateAtoms(property.Value);
                }
                break;
            case JsonValueKind.Array:
                foreach (var item in value.EnumerateArray()) { ValidateAtoms(item); }
                break;
            case JsonValueKind.String:
                ValidateUnicode(value.GetString() ?? throw Failure());
                break;
            case JsonValueKind.Number:
                var raw = value.GetRawText();
                if (raw.IndexOfAny(['.', 'e', 'E']) >= 0 || !value.TryGetInt64(out _)) { throw Failure(); }
                break;
            case JsonValueKind.Null:
                break;
            default:
                throw Failure();
        }
    }

    private static int ValidateUnicode(string value)
    {
        var count = 0;
        for (var index = 0; index < value.Length; index++, count++)
        {
            if (char.IsHighSurrogate(value[index]))
            {
                if (index + 1 >= value.Length || !char.IsLowSurrogate(value[index + 1])) { throw Failure(); }
                index++;
            }
            else if (char.IsLowSurrogate(value[index])) { throw Failure(); }
        }
        return count;
    }

    internal static CryptographicException Failure() => new("Protected cashier state is unavailable.");
}
