#!/usr/bin/env bash
# One-command redeploy to the live AWS Lightsail box.
#
# The box holds a real git clone of this repo (github.com/havishalterx-eng/
# ax-scraper-, public, no auth needed), so redeploying is "pull the new
# commit, rebuild, restart" over SSH rather than local packaging.
#
# Secrets are NOT in this file. The box keeps them in ~/.ax-scraper-env
# (chmod 600), which is read at container start:
#
#   AX_API_TOKEN=...        required; without it the API is unauthenticated
#   AWS_ACCESS_KEY_ID=...   for Bedrock
#   AWS_SECRET_ACCESS_KEY=...
#   AWS_REGION=ap-south-1
#
# Data (agents, run history, signed-in browser profiles) lives in a named
# volume so a rebuild does not wipe it.
#
# Caddy fronts the API and terminates TLS; the app container publishes no port
# of its own, so the plain-HTTP endpoint is not reachable from the internet.
#
# Usage: ./deploy.sh
set -euo pipefail

HOST="ubuntu@13.206.203.227"
KEY="$HOME/.ssh/browser-mcp-lightsail.pem"

ssh -i "$KEY" "$HOST" '
  set -euo pipefail

  if [ ! -f ~/.ax-scraper-env ]; then
    echo "Missing ~/.ax-scraper-env on the box - refusing to deploy an" >&2
    echo "unauthenticated API onto a public host." >&2
    exit 1
  fi
  if ! grep -q "^AX_API_TOKEN=." ~/.ax-scraper-env; then
    echo "AX_API_TOKEN is not set in ~/.ax-scraper-env - refusing to deploy." >&2
    exit 1
  fi

  cd browser-mcp
  git pull --ff-only

  # The old single-container deploy published 8000 directly; compose puts
  # Caddy in front and keeps the app off the public interface.
  sudo docker rm -f browser-mcp 2>/dev/null || true

  # compose reads env_file relative to the project dir, and runs as root, so
  # the secrets file is linked in rather than referenced through ~.
  ln -sf ~/.ax-scraper-env ./.ax-scraper-env

  sudo docker volume create ax-scraper-data >/dev/null
  sudo docker compose up -d --build
  sleep 5
  sudo docker compose ps
  sudo docker compose logs --tail 6 app
'
