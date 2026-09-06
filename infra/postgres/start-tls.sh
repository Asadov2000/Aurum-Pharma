#!/bin/sh
set -eu

tls_dir=/var/lib/postgresql/tls
install -d -m 700 -o postgres -g postgres "$tls_dir"
install -m 644 -o postgres -g postgres /run/tls/ca.crt "$tls_dir/ca.crt"
install -m 644 -o postgres -g postgres /run/tls/postgres.crt "$tls_dir/server.crt"
install -m 600 -o postgres -g postgres /run/tls/postgres.key "$tls_dir/server.key"

exec /usr/local/bin/docker-entrypoint.sh "$@"
