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
# The box is a 2 vCPU / 2GB Lightsail instance and the image it builds contains
# a full Chromium. That build has run fine for months and then took the whole
# host down: sshd could no longer complete a handshake and the API stopped
# answering, while the kernel still accepted TCP connections - memory
# exhaustion, not a crash. A swapfile is provisioned below so the build has
# somewhere to spill instead of taking the machine with it.
#
# Usage: ./deploy.sh
set -euo pipefail

# Static IP, not the instance's own public address: a stop/start reassigns the
# latter, and rebooting is exactly what you do when the box stops answering.
HOST="ubuntu@3.7.135.185"
KEY="$HOME/.ssh/ax-scraper-lightsail.pem"

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

  # Swap, provisioned once and left in place. The build peaks well above what
  # 2GB can hold, and without anywhere to spill the kernel starves userspace -
  # which is how a routine deploy took the host offline and left it needing a
  # console reboot. Swap is slow, and that is the point: a slow build beats an
  # unreachable box.
  if [ "$(swapon --show --noheadings | wc -l)" -eq 0 ]; then
    echo "No swap configured; creating a 2G swapfile."
    sudo fallocate -l 2G /swapfile || sudo dd if=/dev/zero of=/swapfile bs=1M count=2048
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    # Survive a reboot, without duplicating the entry on a re-run.
    grep -q "^/swapfile " /etc/fstab || echo "/swapfile none swap sw 0 0" | sudo tee -a /etc/fstab >/dev/null
    # Spill only under real pressure - this is a safety net for build peaks,
    # not a reason to page out a running scraper.
    sudo sysctl -q vm.swappiness=10
    grep -q "^vm.swappiness" /etc/sysctl.conf || echo "vm.swappiness=10" | sudo tee -a /etc/sysctl.conf >/dev/null
  fi
  free -h | sed -n "1p;3p"

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
