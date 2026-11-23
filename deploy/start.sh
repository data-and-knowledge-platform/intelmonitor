#!/bin/sh
# The site's container start. With the STORE_* variables set, the API pulls the store latest.json
# names from the bucket before it serves, then checks for a newer one every 10 minutes
# (intelmonitor/store/publish.py); there is no volume. The runner, from the same image, has its own
# start command in its service settings (docs/deploy.md).
set -e
exec /app/api/.venv/bin/uvicorn intelmonitor.server.site:site --host 0.0.0.0 --port "${PORT:-8000}"
