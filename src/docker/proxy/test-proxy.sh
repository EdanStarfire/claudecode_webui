#!/usr/bin/env bash
set -euo pipefail

# Configuration
PROXY_IMAGE="claude-proxy:local"
PROXY_CONTAINER="claude-proxy-test"
AGENT_NET="agent-net-test"
BRIDGE_NET="bridge-net-test"
CERTS_DIR="$(cd "$(dirname "$0")" && pwd)/certs"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { echo -e "${GREEN}PASS${NC}: $1"; }
fail() { echo -e "${RED}FAIL${NC}: $1"; FAILURES=$((FAILURES + 1)); }
info() { echo -e "${YELLOW}INFO${NC}: $1"; }

FAILURES=0

cleanup() {
    info "Cleaning up..."
    docker rm -f "$PROXY_CONTAINER" 2>/dev/null || true
    docker rm -f test-agent 2>/dev/null || true
    docker network rm "$AGENT_NET" 2>/dev/null || true
    docker network rm "$BRIDGE_NET" 2>/dev/null || true
    info "Cleanup complete"
}

trap cleanup EXIT

# --- Setup ---
info "Building proxy image..."
docker build -t "$PROXY_IMAGE" "$SCRIPT_DIR"

info "Creating networks..."
# agent-net: specific subnet, no --internal. --internal would add a host-level
# iptables isolation rule that blocks the proxy from forwarding traffic, even
# with IP forwarding enabled. Instead, the proxy's own iptables (PREROUTING +
# MASQUERADE) enforces policy, and mitmproxy's addon enforces the allowlist.
docker network create --subnet 10.100.0.0/24 "$AGENT_NET" 2>/dev/null || true
docker network create "$BRIDGE_NET" 2>/dev/null || true

info "Creating certs directory..."
mkdir -p "$CERTS_DIR"

info "Starting proxy container..."
docker run -d \
    --name "$PROXY_CONTAINER" \
    --network "$BRIDGE_NET" \
    --cap-add NET_ADMIN \
    -v "$CERTS_DIR:/root/.mitmproxy" \
    "$PROXY_IMAGE"

# Attach proxy to agent network with a static IP so PROXY_IP is predictable.
# Agent containers add a default route to this IP via ip route, routing all
# their traffic through the proxy for transparent interception.
PROXY_IP="10.100.0.1"
docker network connect --ip "$PROXY_IP" "$AGENT_NET" "$PROXY_CONTAINER"

# Wait for proxy to initialize
info "Waiting for proxy to start..."
sleep 5

info "Proxy IP on agent network: $PROXY_IP"

# Verify CA cert was generated
if [ -f "$CERTS_DIR/mitmproxy-ca-cert.pem" ]; then
    pass "CA certificate generated at $CERTS_DIR/mitmproxy-ca-cert.pem"
else
    fail "CA certificate not found"
    exit 1
fi

# --- Helper: run test container with transparent proxy routing ---
# Agent containers use --cap-add NET_ADMIN to add a default route through the
# proxy. This routes all TCP through the proxy's iptables PREROUTING rules,
# which redirect ports 80/443 to mitmdump (transparent mode). No explicit
# proxy env vars needed — interception is transparent to the application.
# Uses alpine as base (wget, nslookup, nc, ip all included via busybox).
run_agent() {
    local name="$1"
    local image="$2"
    local cmd="$3"
    docker run --rm \
        --name test-agent \
        --network "$AGENT_NET" \
        --dns "$PROXY_IP" \
        --cap-add NET_ADMIN \
        -e "SSL_CERT_FILE=/etc/proxy-ca/mitmproxy-ca-cert.pem" \
        -e "NODE_EXTRA_CA_CERTS=/etc/proxy-ca/mitmproxy-ca-cert.pem" \
        -v "$CERTS_DIR/mitmproxy-ca-cert.pem:/etc/proxy-ca/mitmproxy-ca-cert.pem:ro" \
        --entrypoint sh \
        "$image" \
        -c "ip route add default via $PROXY_IP 2>/dev/null || true; $cmd"
}

# --- Test 1: Allowlisted domain (HTTPS, transparent interception) ---
# No proxy env vars. Traffic routes through proxy via iptables REDIRECT.
# mitmdump intercepts TLS, presents mitmproxy CA-signed cert; wget trusts it.
info "Test 1: HTTPS request to allowlisted domain (api.github.com) — transparent..."
if run_agent "test-allow" alpine \
    "wget -qO- --ca-certificate=/etc/proxy-ca/mitmproxy-ca-cert.pem https://api.github.com/ >/dev/null" \
    >/dev/null 2>&1; then
    pass "Allowlisted HTTPS request succeeded (transparently intercepted)"
else
    fail "Allowlisted HTTPS request failed"
fi

# --- Test 2: Non-allowlisted domain (HTTPS, transparent interception) ---
# DNS returns NXDOMAIN for example.com (CoreDNS catch-all block).
info "Test 2: HTTPS request to non-allowlisted domain (example.com) — transparent..."
if run_agent "test-deny" alpine \
    "wget -qO- --ca-certificate=/etc/proxy-ca/mitmproxy-ca-cert.pem https://example.com/ >/dev/null 2>&1"; then
    fail "Non-allowlisted HTTPS request should have been blocked"
else
    pass "Non-allowlisted HTTPS request blocked (DNS NXDOMAIN or mitmproxy 403)"
fi

# --- Test 3: DNS allowlisted domain ---
info "Test 3: DNS resolution for allowlisted domain..."
if run_agent "test-dns-allow" alpine \
    "nslookup api.github.com $PROXY_IP >/dev/null 2>&1" \
    >/dev/null 2>&1; then
    pass "DNS resolution for allowlisted domain succeeded"
else
    fail "DNS resolution for allowlisted domain failed"
fi

# --- Test 4: DNS non-allowlisted domain (NXDOMAIN) ---
info "Test 4: DNS resolution for non-allowlisted domain..."
if run_agent "test-dns-deny" alpine \
    "nslookup example.com $PROXY_IP >/dev/null 2>&1" \
    >/dev/null 2>&1; then
    fail "DNS resolution for non-allowlisted domain should have failed"
else
    pass "DNS for non-allowlisted domain returned NXDOMAIN"
fi

# --- Test 5: Node.js native fetch to non-allowlisted domain ---
# No proxy env vars. DNS NXDOMAIN prevents resolution.
info "Test 5: Node.js native fetch to non-allowlisted domain (transparent)..."
if run_agent "test-node-bypass" node:22-slim \
    "node -e \"fetch('https://blocked.example.com').then(r => { console.log(r.status); process.exit(0); }).catch(e => { console.error(e.cause?.code || e.message); process.exit(1); })\"" \
    >/dev/null 2>&1; then
    fail "Node.js fetch to non-allowlisted domain should have failed"
else
    pass "Node.js fetch blocked for non-allowlisted domain (DNS-level)"
fi

# --- Test 6: Direct TCP to raw IP (bypass DNS, test iptables interception) ---
# Connects to a raw IP (example.com's address) on port 80, bypassing DNS.
# iptables PREROUTING redirects this to mitmdump transparent mode.
# The IP does not match any allowlisted domain → mitmproxy blocks it.
info "Test 6: Direct TCP to raw IP — iptables transparent interception..."
if run_agent "test-direct" alpine \
    "timeout 5 nc -w 3 93.184.216.34 80 </dev/null 2>/dev/null" \
    >/dev/null 2>&1; then
    fail "Direct TCP to raw IP should have been intercepted and blocked"
else
    pass "Direct TCP to raw IP blocked (transparently intercepted by iptables)"
fi

# --- Results ---
echo ""
echo "=============================="
if [ $FAILURES -eq 0 ]; then
    echo -e "${GREEN}All tests passed${NC}"
    exit 0
else
    echo -e "${RED}$FAILURES test(s) failed${NC}"
    exit 1
fi
