#!/bin/sh
# Preserve the existing Redis database and queue names; isolate worker capabilities.
set -eu
umask 077

read_secret() {
    value="$(printenv "$1" || true)"
    if [ -z "$value" ] && [ -f "/run/secrets/$1" ]; then
        value="$(cat "/run/secrets/$1")"
    fi
    printf '%s' "$value"
}

password_rule() {
    if [ -z "$1" ]; then
        echo 'A Redis worker password is missing' >&2
        exit 1
    fi
    hashed="$(printf '%s' "$1" | sha256sum)"
    printf '#%s' "${hashed%% *}"
}

queue_keys() {
    # These are actual Kombu priority separator bytes, not literal backslashes.
    printf ' ~%s ~%s\006\0263 ~%s\006\0266 ~%s\006\0269 ~_kombu.binding.%s' \
        "$1" "$1" "$1" "$1" "$1"
}

worker_user() {
    role="$1"
    queue="$2"
    password_name="$3"
    printf 'user aurum_%s reset on %s resetchannels' \
        "$role" "$(password_rule "$(read_secret "$password_name")")"
    queue_keys "$queue"
    printf ' ~aurum:broker:%s:unacked ~aurum:broker:%s:unacked_index ~aurum:broker:%s:unacked_mutex' \
        "$queue" "$queue" "$queue"
    printf ' +ping +lpush +llen +sadd +smembers +multi +exec +client|setinfo'
    printf ' +brpop +rpush +hset +hget +hdel +zadd +zrem +zrevrangebyscore'
    printf ' +watch +unwatch +set +get +del +evalsha +script|load\n'
}

default_password="$(read_secret REDIS_PASSWORD)"
if [ -z "$default_password" ]; then
    echo 'The application Redis password is missing' >&2
    exit 1
fi
password_hashes="$(password_rule "$default_password")"
for password_name in REDIS_CELERY_API_PASSWORD REDIS_CELERY_BEAT_PASSWORD \
    REDIS_CELERY_SYSTEM_PASSWORD REDIS_CELERY_CATALOG_PASSWORD \
    REDIS_CELERY_BILLING_PASSWORD REDIS_CELERY_MAILER_PASSWORD; do
    candidate_password="$(read_secret "$password_name")"
    if [ -z "$candidate_password" ]; then
        echo 'A Redis worker password is missing' >&2
        exit 1
    fi
    candidate_hash="$(password_rule "$candidate_password")"
    case " $password_hashes " in
        *" $candidate_hash "*)
            echo 'Redis application and worker passwords must be distinct' >&2
            exit 1
            ;;
    esac
    password_hashes="$password_hashes $candidate_hash"
done
default_auth="$(password_rule "$default_password")"

{
    printf 'user default reset on %s ~* &* +@all -acl -config -debug -flushall -flushdb -module -shutdown -slaveof -replicaof\n' "$default_auth"
    printf 'user aurum_api reset on %s resetchannels' \
        "$(password_rule "$(read_secret REDIS_CELERY_API_PASSWORD)")"
    queue_keys catalog-worker
    printf ' +ping +lpush +llen +sadd +smembers +multi +exec +client|setinfo\n'
    printf 'user aurum_beat reset on %s resetchannels' \
        "$(password_rule "$(read_secret REDIS_CELERY_BEAT_PASSWORD)")"
    queue_keys default
    queue_keys billing-worker
    queue_keys platform-mailer
    printf ' +ping +lpush +llen +sadd +smembers +multi +exec +client|setinfo\n'
    worker_user system default REDIS_CELERY_SYSTEM_PASSWORD
    worker_user catalog catalog-worker REDIS_CELERY_CATALOG_PASSWORD
    worker_user billing billing-worker REDIS_CELERY_BILLING_PASSWORD
    worker_user mailer platform-mailer REDIS_CELERY_MAILER_PASSWORD
} > /tmp/aurum-users.acl
chown redis:redis /tmp/aurum-users.acl

set -- --appendonly "${REDIS_APPENDONLY:-yes}" --aclfile /tmp/aurum-users.acl \
    --protected-mode yes --maxmemory "${AURUM_REDIS_MAXMEMORY:-384mb}" --maxmemory-policy noeviction
case "${REDIS_TLS:-false}" in
    true)
        mkdir -p /tmp/aurum-redis-tls
        cp /run/tls/ca.crt /run/tls/redis.crt /run/tls/redis.key /tmp/aurum-redis-tls/
        chown redis:redis /tmp/aurum-redis-tls /tmp/aurum-redis-tls/*
        chmod 600 /tmp/aurum-redis-tls/redis.key
        set -- "$@" --port 0 --tls-port 6379 \
            --tls-cert-file /tmp/aurum-redis-tls/redis.crt \
            --tls-key-file /tmp/aurum-redis-tls/redis.key \
            --tls-ca-cert-file /tmp/aurum-redis-tls/ca.crt \
            --tls-auth-clients no --tls-protocols 'TLSv1.2 TLSv1.3'
        ;;
    false) set -- "$@" --port 6379 ;;
    *) echo 'REDIS_TLS must be true or false' >&2; exit 1 ;;
esac
exec /usr/local/bin/docker-entrypoint.sh redis-server "$@"
