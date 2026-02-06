#!/bin/zsh
set -euo pipefail
cd /Users/gioalers/clawd
set -a
source .env.retell
set +a
exec /opt/homebrew/bin/node /Users/gioalers/clawd/retell-reverse-proxy.mjs
