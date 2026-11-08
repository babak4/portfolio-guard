#!/usr/bin/env bash
# Unverified: written from memory, run it once and correct this file.
# Checks that nothing but loopback listens for the Guard host.
#
#   ops/portscan.sh local              run ON the host: only 127.0.0.1 / ::1 (and Tailscale) may listen
#   ops/portscan.sh remote <ip-or-host> run from OFF the tailnet: every TCP port must be closed or filtered
set -euo pipefail

MODE="${1:?usage: portscan.sh local | remote <public-ip-or-hostname>}"

case "$MODE" in
  local)
    command -v ss >/dev/null || { echo "ss not installed (iproute2)"; exit 2; }
    # Columns: Netid State Recv-Q Send-Q Local-Address:Port ... Keep listeners not bound to loopback.
    bad="$(ss -H -lntu | awk '{print $5}' \
      | grep -Ev '^(127\.0\.0\.1|\[::1\]|::1|127\.0\.0\.53%lo|\[?fe80:)' || true)"
    if [ -n "$bad" ]; then
      echo "FAIL: listeners not bound to loopback:"
      echo "$bad"
      echo "(Docker publishes 127.0.0.1:8501 only. SSH on :22 is expected unless ufw/firewall blocks it"
      echo " from the internet; confirm with the remote scan.)"
      exit 1
    fi
    echo "PASS: only loopback listeners"
    ;;
  remote)
    TARGET="${2:?usage: portscan.sh remote <public-ip-or-hostname>}"
    command -v nmap >/dev/null || { echo "nmap not installed"; exit 2; }
    if command -v tailscale >/dev/null && tailscale status >/dev/null 2>&1; then
      echo "This machine is on a tailnet; run 'tailscale down' first so the scan takes the public path."
      exit 2
    fi
    OUT="portscan-$(date +%Y%m%d-%H%M%S).txt"
    nmap -Pn -p- --min-rate 2000 -T4 --open -oG - "$TARGET" | tee "$OUT"
    if grep -E "/open/" "$OUT" >/dev/null; then
      echo "FAIL: open ports found (see $OUT)"
      exit 1
    fi
    echo "PASS: no open TCP ports. Record the file name and date in docs/paper-trading.md."
    ;;
  *)
    echo "usage: portscan.sh local | remote <public-ip-or-hostname>"; exit 2;;
esac
