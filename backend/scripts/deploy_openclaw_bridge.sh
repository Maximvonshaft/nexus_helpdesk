#!/bin/bash
set -euo pipefail

# Deploy OpenClaw Bridge Server

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKEND_DIR="$REPO_ROOT/backend"
BRIDGE_SCRIPT="$BACKEND_DIR/scripts/openclaw_bridge_server.js"
LOG_DIR="$HOME/.openclaw/logs"
LOG_FILE="$LOG_DIR/openclaw_bridge.log"

echo "Deploying OpenClaw Bridge from $BRIDGE_SCRIPT..."

# Get current git sha
export BRIDGE_GIT_SHA=$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo "unknown")

mkdir -p "$LOG_DIR"

# Stop existing server
echo "Stopping old openclaw_bridge_server.js..."
pkill -f "node $BRIDGE_SCRIPT" || echo "No existing bridge server found."
sleep 2

# Start new server
echo "Starting new openclaw_bridge_server.js..."
export OPENCLAW_GATEWAY_RUNTIME_MODULE="/home/vboxuser/.openclaw/tools/node-v22.22.0/lib/node_modules/openclaw/dist/plugin-sdk/gateway-runtime.js"
export OPENCLAW_BRIDGE_GATEWAY_SCOPES="operator.read,operator.write"
nohup node "$BRIDGE_SCRIPT" > "$LOG_FILE" 2>&1 &
NEW_PID=$!
echo "Bridge started with PID $NEW_PID."
sleep 3

# Verify health
echo "Checking /health endpoint..."
HEALTH_RES=$(curl -s http://127.0.0.1:18792/health || echo '{"ok": false}')
OK=$(echo "$HEALTH_RES" | grep -o '"ok":true' || echo 'false')

if [ "$OK" != '"ok":true' ]; then
    echo "ERROR: Bridge /health failed."
    echo "$HEALTH_RES"
    exit 1
fi

echo "Bridge /health is OK."
ALLOW_WRITES=$(echo "$HEALTH_RES" | grep -o '"allowWrites":false' || echo 'unknown')
SEND_MESSAGE_ENABLED=$(echo "$HEALTH_RES" | grep -o '"sendMessageEnabled":false' || echo 'unknown')
AI_REPLY_SESSION_KEY=$(echo "$HEALTH_RES" | grep -o '"aiReplySessionKey":"[^"]*"' || echo 'unknown')

echo "Status flags:"
echo " - $ALLOW_WRITES"
echo " - $SEND_MESSAGE_ENABLED"
echo " - $AI_REPLY_SESSION_KEY"

# Execute nonce smoke
NONCE="NEXUS_SMOKE_$(date +%Y%m%d_%H%M%S)_$RANDOM"
echo "Executing nonce smoke with $NONCE..."
SMOKE_RES=$(curl -s -X POST -H "Content-Type: application/json" \
    -d "{\"sessionKey\":\"webchat-ai-deploy-smoke\", \"prompt\":\"Return exactly this nonce string and nothing else: ${NONCE}\"}" \
    http://127.0.0.1:18792/ai-reply)

if echo "$SMOKE_RES" | grep -q "$NONCE"; then
    echo "SUCCESS: Nonce smoke passed. Reply contains nonce."
    echo "Smoke output: $SMOKE_RES"
else
    echo "ERROR: Nonce smoke failed. Reply does not contain nonce."
    echo "Smoke output: $SMOKE_RES"
    exit 1
fi

echo "Deployment successful."
