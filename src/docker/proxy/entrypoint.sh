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

# --- Enable IP forwarding and NAT ---
# Required to act as a gateway: forward packets from agent-net to internet
# (bridge-net), and masquerade the source IP so return traffic routes back.
echo "Enabling IP forwarding and NAT..."
echo 1 > /proc/sys/net/ipv4/ip_forward
iptables -t nat -A POSTROUTING -j MASQUERADE
iptables -A FORWARD -j ACCEPT

# --- Set up iptables for transparent proxying ---
# Redirect all TCP 80/443 arriving at any interface to mitmdump on :8080.
# No -i restriction: the proxy container connects to multiple Docker networks
# (bridge-net for internet egress, agent-net for agent traffic) and the
# interface name for agent-net is not predictable at build time.
# Agent containers route through the proxy as their default gateway, so their
# traffic arrives at the proxy's agent-net interface and is caught here.
# Exclude traffic originating from this container itself (OUTPUT chain handles
# local traffic separately; PREROUTING only sees forwarded/incoming packets).
echo "Configuring iptables for transparent proxy..."
iptables -t nat -A PREROUTING -p tcp --dport 80 -j REDIRECT --to-port 8080
iptables -t nat -A PREROUTING -p tcp --dport 443 -j REDIRECT --to-port 8080

# --- Start mitmdump in transparent mode ---
# mitmdump is the headless version of mitmproxy (no interactive TUI).
# Transparent mode uses SO_ORIGINAL_DST to recover the original destination
# after iptables REDIRECT has rewritten it, enabling MITM of all TCP 80/443
# regardless of whether the client has proxy env vars set.
echo "Starting mitmdump (transparent mode) on :8080..."
echo "Addon: /etc/proxy/addon.py"
exec mitmdump --mode transparent --showhost \
    --set confdir="$CERTS_DIR" \
    --listen-port 8080 \
    --ssl-insecure \
    -s /etc/proxy/addon.py \
    --set block_global=false \
    --set stream_large_bodies=1m
