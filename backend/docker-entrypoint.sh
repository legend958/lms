#!/bin/sh
set -eu

# Render mounts a persistent disk root-owned. The application runs as the
# unprivileged "app" user, so take ownership of the uploads volume once per
# boot and then drop privileges. Without this, uploads fail with EACCES under
# /data even though the directory exists in the image.
if [ "$(id -u)" = "0" ]; then
    mkdir -p "${UPLOAD_DIR:-/data/uploads}"
    chown -R app:app /data
    # Render's Docker Command is a command line, but the runner may hand it to
    # us either pre-split or as one string. Wrap a single string in a shell so
    # both forms execute the same way.
    if [ "$#" -eq 1 ]; then
        set -- /bin/sh -c "$1"
    fi
    exec setpriv \
        --reuid="$(id -u app)" \
        --regid="$(id -g app)" \
        --init-groups \
        "$@"
fi

# Already unprivileged (local compose): normalise the same single-string form.
if [ "$#" -eq 1 ]; then
    set -- /bin/sh -c "$1"
fi

exec "$@"
