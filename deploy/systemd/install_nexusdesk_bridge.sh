#!/usr/bin/env bash
set -euo pipefail

SERVICE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_DIR="/etc/systemd/system"

install_unit() {
  local name="$1"
  install -m 0644 "$SERVICE_DIR/$name" "$TARGET_DIR/$name"
}

install_unit nexusdesk-openclaw-bridge.service
install_unit nexusdesk-api.service
install_unit nexusdesk-worker.service

systemctl daemon-reload
systemctl enable nexusdesk-openclaw-bridge.service
systemctl restart nexusdesk-openclaw-bridge.service
systemctl restart nexusdesk-api.service
systemctl restart nexusdesk-worker.service

echo "Installed and restarted:"
echo "  - nexusdesk-openclaw-bridge.service"
echo "  - nexusdesk-api.service"
echo "  - nexusdesk-worker.service"
echo
echo "Check status with:"
echo "  systemctl status nexusdesk-openclaw-bridge.service"
echo "  journalctl -u nexusdesk-openclaw-bridge.service -f"
