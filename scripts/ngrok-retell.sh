#!/bin/zsh
set -euo pipefail
# Expose the local Retell reverse proxy (3336) to the internet.
exec /opt/homebrew/bin/ngrok http 3336
