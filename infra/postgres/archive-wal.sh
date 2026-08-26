#!/bin/sh
set -eu

umask 027

source_path="${1:-}"
archive_name="${2:-}"

[ -f "$source_path" ] || {
    echo "WAL source is not a regular file" >&2
    exit 1
}
[ -n "$archive_name" ] && [ "$(basename -- "$archive_name")" = "$archive_name" ] || {
    echo "Invalid WAL archive name" >&2
    exit 1
}

destination="/wal-archive/$archive_name.gz"
temporary="/wal-archive/.${archive_name}.$$.gz"
trap 'rm -f -- "$temporary"' EXIT INT TERM
gzip -n -c "$source_path" > "$temporary"
chmod 0640 "$temporary"
if [ -e "$destination" ]; then
    cmp -s "$temporary" "$destination" && exit 0
    echo "Refusing to overwrite a different archived WAL file: $archive_name" >&2
    exit 1
fi
sync
mv -- "$temporary" "$destination"
sync
trap - EXIT INT TERM
