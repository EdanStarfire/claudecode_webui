#!/usr/bin/env bash
set -euo pipefail

CERTS_DIR="/root/.mitmproxy"
ALLOWLIST="/etc/proxy/allowlist.json"
COREFILE="/etc/coredns/Corefile"

echo "=== Proxy Container Starting ==="

# --- Generate CoreDNS config from allowlist ---
echo "Generating Corefile from allowlist..."
DOMAINS=$(jq -r '.domains[]' "$ALLOWLIST")

cat > "$COREFILE" <<'HEADER'
# Auto-generated from allowlist.json
HEADER

for domain in $DOMAINS; do
    cat >> "$COREFILE" <<EOF

${domain} {
    forward . 8.8.8.8 8.8.4.4
    log
}
EOF
done

cat >> "$COREFILE" <<'CATCHALL'

. {
    template IN A {
        rcode NXDOMAIN
    }
    template IN AAAA {
        rcode NXDOMAIN
    }
    log
}
CATCHALL

echo "Corefile generated with $(echo "$DOMAINS" | wc -w) domains"

# --- Start CoreDNS in background ---
echo "Starting CoreDNS on :53..."
coredns -conf "$COREFILE" -dns.port 53 &
COREDNS_PID=$!

# --- Generate CA cert if not present ---
if [ ! -f "$CERTS_DIR/mitmproxy-ca-cert.pem" ]; then
    echo "Generating mitmproxy CA certificate..."
    mitmdump --set confdir="$CERTS_DIR" -q &
    MITM_INIT_PID=$!
    sleep 2
    kill "$MITM_INIT_PID" 2>/dev/null || true
    wait "$MITM_INIT_PID" 2>/dev/null || true
    echo "CA certificate generated at $CERTS_DIR/mitmproxy-ca-cert.pem"
else
    echo "Using existing CA certificate"
fi

# --- Set up iptables for transparent proxying ---
# Both ports redirect to 8080 — mitmdump transparent mode auto-detects TLS
# by inspecting the ClientHello on the same listener port.
echo "Configuring iptables for transparent proxy..."
iptables -t nat -A PREROUTING -i eth0 -p tcp --dport 80 -j REDIRECT --to-port 8080
iptables -t nat -A PREROUTING -i eth0 -p tcp --dport 443 -j REDIRECT --to-port 8080

# --- Start mitmdump as explicit HTTP/HTTPS proxy ---
# mitmdump is the headless version of mitmproxy (no interactive TUI).
# Default (regular) mode accepts explicit HTTP CONNECT proxy requests.
# Agent containers set http_proxy/https_proxy env vars pointing to port 8080.
# For HTTPS, mitmdump performs MITM via CONNECT interception + cert injection.
# The iptables rules above additionally redirect any direct port 80/443 traffic
# (e.g., from tools that bypass proxy env vars) to port 8080.
echo "Starting mitmdump (explicit proxy mode) on :8080..."
echo "Addon: /etc/proxy/addon.py"
exec mitmdump --showhost \
    --set confdir="$CERTS_DIR" \
    --listen-port 8080 \
    --ssl-insecure \
    -s /etc/proxy/addon.py \
    --set block_global=false \
    --set stream_large_bodies=1m
