#!/usr/bin/env bash

# Indicate information from the control environment
export CONTROL_VERSION=0.0.2
export CONTROL_COMMIT=$(git rev-parse HEAD)

# Load environment variables similar to how docker compose does it
set -a
source .env
set +a

# Load GPGHOME variable into invoke variable to use it in the management service
if [ -n "${INVOKE_MANAGEMENT_CREDENTIALS_GPGHOME:-}" ]; then
    export INVOKE_MANAGEMENT_CREDENTIALS_GPGHOME
elif [ -n "${GNUPGHOME:-}" ]; then
    export INVOKE_MANAGEMENT_CREDENTIALS_GPGHOME="${GNUPGHOME}"
else
    export INVOKE_MANAGEMENT_CREDENTIALS_GPGHOME="$HOME/.gnupg"
fi

source venv/bin/activate
