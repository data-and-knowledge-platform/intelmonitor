#!/bin/sh
# Deploy the code to Railway: ./scripts/deploy.sh
# Uploads the repo's tracked and unignored files to both services, `site` and `runner`, so their
# code and schema match. Data never ships with the code: the runner publishes stores to the bucket
# and the site pulls them (docs/deploy.md).
set -e
root=$(cd "$(dirname "$0")/.." && pwd)
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
cd "$root"
git ls-files -co --exclude-standard -z | xargs -0 -I{} sh -c '[ -f "$1" ] && mkdir -p "$2/$(dirname "$1")" && cp "$1" "$2/$1" || true' _ {} "$stage"
message="${DEPLOY_MESSAGE:-$(git log -1 --format=%s)}"
for service in site runner; do
  railway up "$stage" --path-as-root --no-gitignore --service "$service" --detach -m "$message"
done
