#!/bin/sh
set -eu

# Render mounts a persistent disk root-owned. The application runs as the
# unprivileged "app" user, so take ownership of the uploads volume once per
# boot and then drop privileges. Without this, uploads fail with EACCES under
# /data even though the directory exists in the image.
if [ "$(id -u)" = "0" ]; then
    mkdir -p "${UPLOAD_DIR:-/data/uploads}"
    chown -R app:app /data
    exec setpriv \
        --reuid="$(id -u app)" \
        --regid="$(id -g app)" \
        --init-groups \
        "$@"
fi

exec "$@"
