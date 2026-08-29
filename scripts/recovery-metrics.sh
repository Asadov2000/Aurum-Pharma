#!/bin/sh

# Prometheus textfile metrics for host-scheduled recovery jobs. The directory is
# optional for manual development runs and mandatory in the systemd units.

aurum_record_recovery_metric() {
    component="$1"
    status="$2"
    started_at="$3"
    finished_at="$4"
    metrics_dir="${AURUM_RECOVERY_METRICS_DIR:-}"

    [ -n "$metrics_dir" ] || return 0
    case "$component" in
        ""|*[!a-z0-9_]*)
            echo "Invalid recovery metrics component: $component" >&2
            return 1
            ;;
    esac
    [ -d "$metrics_dir" ] && [ -w "$metrics_dir" ] || {
        echo "Recovery metrics directory is not writable: $metrics_dir" >&2
        return 1
    }

    metric_file="$metrics_dir/aurum-recovery-$component.prom"
    last_success=0
    if [ -f "$metric_file" ]; then
        last_success="$(sed -n \
            's/^aurum_recovery_job_last_success_timestamp_seconds{component="[a-z0-9_]*"} \([0-9][0-9]*\)$/\1/p' \
            "$metric_file" | head -n 1)"
        last_success="${last_success:-0}"
    fi
    if [ "$status" -eq 0 ]; then
        last_success="$finished_at"
        success=1
    else
        success=0
    fi
    duration=$((finished_at - started_at))
    [ "$duration" -ge 0 ] || duration=0

    temporary="$(mktemp "$metrics_dir/.aurum-recovery-$component.XXXXXX")"
    cat > "$temporary" <<EOF
# HELP aurum_recovery_job_last_run_timestamp_seconds Unix time of the latest recovery job attempt.
# TYPE aurum_recovery_job_last_run_timestamp_seconds gauge
aurum_recovery_job_last_run_timestamp_seconds{component="$component"} $finished_at
# HELP aurum_recovery_job_last_success_timestamp_seconds Unix time of the latest successful recovery job.
# TYPE aurum_recovery_job_last_success_timestamp_seconds gauge
aurum_recovery_job_last_success_timestamp_seconds{component="$component"} $last_success
# HELP aurum_recovery_job_last_status Whether the latest recovery job succeeded (1) or failed (0).
# TYPE aurum_recovery_job_last_status gauge
aurum_recovery_job_last_status{component="$component"} $success
# HELP aurum_recovery_job_last_duration_seconds Duration of the latest recovery job attempt.
# TYPE aurum_recovery_job_last_duration_seconds gauge
aurum_recovery_job_last_duration_seconds{component="$component"} $duration
EOF
    chmod 0644 "$temporary"
    mv -f "$temporary" "$metric_file"
}

aurum_run_recovery_step() {
    component="$1"
    shift
    started_at="$(date +%s)"

    set +e
    "$@"
    status=$?
    set -e

    finished_at="$(date +%s)"
    aurum_record_recovery_metric \
        "$component" "$status" "$started_at" "$finished_at"
    return "$status"
}
