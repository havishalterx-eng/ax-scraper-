#!/usr/bin/env bash
# One-command redeploy to the live AWS Lightsail box.
#
# Replaces the old tar/scp/build cycle: the box now holds a real git clone
# of this repo (github.com/havishalterx-eng/ax-scraper-, public, no auth
# needed), so redeploying is just "pull the new commit, rebuild, restart" -
# run over SSH, not local packaging.
#
# Usage: ./deploy.sh
set -euo pipefail

HOST="ubuntu@13.206.203.227"
KEY="$HOME/.ssh/browser-mcp-lightsail.pem"

ssh -i "$KEY" "$HOST" '
  set -euo pipefail
  cd browser-mcp
  git pull
  sudo docker build -t browser-mcp:latest .
  sudo docker stop browser-mcp
  sudo docker rm browser-mcp
  sudo docker run -d --name browser-mcp --restart unless-stopped -p 8000:8000 browser-mcp:latest
  sleep 2
  sudo docker logs --tail 5 browser-mcp
'
