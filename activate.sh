#!/usr/bin/env bash

# Indicate information from the control environment
export CONTROL_VERSION=0.0.1
export CONTROL_COMMIT=$(git rev-parse HEAD)

# Load environment variables similar to how docker compose does it
set -a
source .env
set +a

source venv/bin/activate
