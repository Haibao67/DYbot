#!/bin/sh
# Run as the dzmmbot service account. Never enable shell tracing here.
set -eu
set -a
. /etc/dzmm/dzmm.env
set +a
cd /srv/dzmm/app
exec /srv/dzmm/app/.venv/bin/python -m dzmm_bot.manage "$@"
