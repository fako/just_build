#!/usr/bin/env bash
set -euo pipefail


docker compose \
  --env-file .env \
  --file docker-compose.yml \
  --file services/daemon/docker-compose.daemon.yml \
  --profile workspaces \
  up -d --wait
