#!/bin/zsh
set -euo pipefail

# launchd jobs often have a minimal PATH. Ensure Homebrew binaries (node, etc.) are available,
# especially for any child processes that use /usr/bin/env node.
export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

cd /Users/gioalers/clawd
set -a
source .env.retell
set +a
exec /opt/homebrew/bin/node /Users/gioalers/clawd/retell-sync-bridge.mjs
