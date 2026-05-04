#!/bin/bash
set -euo pipefail

echo "Probing OpenClaw Bridge Health..."

echo "1. Checking /health..."
HEALTH_RES=$(curl -s http://127.0.0.1:18792/health || echo '{"ok": false}')
OK=$(echo "$HEALTH_RES" | grep -o '"ok":true' || echo 'false')

if [ "$OK" != '"ok":true' ]; then
    echo "ERROR: Bridge /health failed."
    echo "$HEALTH_RES"
    exit 1
fi
echo "Bridge /health is OK."

# Checking properties
echo "$HEALTH_RES" | grep -q '"allowWrites":false' && echo "[PASS] allowWrites=false" || echo "[FAIL] allowWrites is not false"
echo "$HEALTH_RES" | grep -q '"sendMessageEnabled":false' && echo "[PASS] sendMessageEnabled=false" || echo "[FAIL] sendMessageEnabled is not false"
echo "$HEALTH_RES" | grep -q '"connected":true' && echo "[PASS] gateway.connected=true" || echo "[FAIL] gateway.connected is not true"
AI_SESSION_KEY=$(echo "$HEALTH_RES" | grep -o '"aiReplySessionKey":"[^"]*"' | cut -d':' -f2-)
if [ -n "$AI_SESSION_KEY" ]; then
    echo "[PASS] aiReplySessionKey is non-empty: $AI_SESSION_KEY"
else
    echo "[FAIL] aiReplySessionKey is empty"
fi

echo "2. Executing /ai-reply nonce smoke..."
NONCE="NEXUS_SMOKE_$(date +%Y%m%d_%H%M%S)_$RANDOM"
SMOKE_RES=$(curl -s -X POST -H "Content-Type: application/json" \
    -d "{\"sessionKey\":\"webchat-ai-probe-smoke\", \"prompt\":\"Return exactly this nonce string and nothing else: ${NONCE}\"}" \
    http://127.0.0.1:18792/ai-reply)

if echo "$SMOKE_RES" | grep -q "$NONCE"; then
    echo "[PASS] Nonce smoke successful. Reply contains nonce."
else
    echo "[FAIL] Nonce smoke failed. Reply does not contain nonce."
    echo "Output: $SMOKE_RES"
    exit 1
fi

if echo "$SMOKE_RES" | grep -q 'session not found'; then
    echo "[FAIL] 'session not found' error appeared in response."
    echo "Output: $SMOKE_RES"
    exit 1
else
    echo "[PASS] No 'session not found' error."
fi

echo "Probe completed successfully."
