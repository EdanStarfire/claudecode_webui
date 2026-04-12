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
docker network create --internal "$AGENT_NET" 2>/dev/null || true
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

# Attach proxy to agent network
docker network connect "$AGENT_NET" "$PROXY_CONTAINER"

# Wait for proxy to initialize
info "Waiting for proxy to start..."
sleep 5

# Get proxy IP on agent network
PROXY_IP=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{if eq .NetworkID "'$(docker network inspect "$AGENT_NET" -f '{{.Id}}')'"}}{{.IPAddress}}{{end}}{{end}}' "$PROXY_CONTAINER")

if [ -z "$PROXY_IP" ]; then
    # Fallback: try simpler inspection
    PROXY_IP=$(docker inspect "$PROXY_CONTAINER" --format '{{json .NetworkSettings.Networks}}' | jq -r '."'"$AGENT_NET"'".IPAddress')
fi

info "Proxy IP on agent network: $PROXY_IP"

# Verify CA cert was generated
if [ -f "$CERTS_DIR/mitmproxy-ca-cert.pem" ]; then
    pass "CA certificate generated at $CERTS_DIR/mitmproxy-ca-cert.pem"
else
    fail "CA certificate not found"
    exit 1
fi

# --- Helper: run test container ---
run_agent() {
    local name="$1"
    shift
    docker run --rm \
        --name test-agent \
        --network "$AGENT_NET" \
        --dns "$PROXY_IP" \
        -e "REQUESTS_CA_BUNDLE=/etc/proxy-ca/mitmproxy-ca-cert.pem" \
        -e "SSL_CERT_FILE=/etc/proxy-ca/mitmproxy-ca-cert.pem" \
        -e "NODE_EXTRA_CA_CERTS=/etc/proxy-ca/mitmproxy-ca-cert.pem" \
        -e "CURL_CA_BUNDLE=/etc/proxy-ca/mitmproxy-ca-cert.pem" \
        -e "GIT_SSL_CAINFO=/etc/proxy-ca/mitmproxy-ca-cert.pem" \
        -e "http_proxy=http://$PROXY_IP:8080" \
        -e "https_proxy=http://$PROXY_IP:8080" \
        -e "HTTP_PROXY=http://$PROXY_IP:8080" \
        -e "HTTPS_PROXY=http://$PROXY_IP:8080" \
        -v "$CERTS_DIR/mitmproxy-ca-cert.pem:/etc/proxy-ca/mitmproxy-ca-cert.pem:ro" \
        "$@"
}

# --- Test 1: Allowlisted domain (HTTPS) ---
info "Test 1: HTTPS request to allowlisted domain (api.github.com)..."
if run_agent "test-allow" debian:bookworm-slim \
    bash -c "apt-get update -qq && apt-get install -y -qq curl >/dev/null 2>&1 && curl -sf --max-time 15 --cacert /etc/proxy-ca/mitmproxy-ca-cert.pem https://api.github.com/ >/dev/null 2>&1"; then
    pass "Allowlisted HTTPS request succeeded"
else
    fail "Allowlisted HTTPS request failed"
fi

# --- Test 2: Non-allowlisted domain (HTTPS) ---
info "Test 2: HTTPS request to non-allowlisted domain (example.com)..."
HTTP_CODE=$(run_agent "test-deny" debian:bookworm-slim \
    bash -c "apt-get update -qq && apt-get install -y -qq curl >/dev/null 2>&1 && curl -s --max-time 15 --cacert /etc/proxy-ca/mitmproxy-ca-cert.pem -o /dev/null -w '%{http_code}' https://example.com/ 2>/dev/null || echo 'BLOCKED'" || echo "BLOCKED")

if [ "$HTTP_CODE" = "403" ] || [ "$HTTP_CODE" = "BLOCKED" ]; then
    pass "Non-allowlisted HTTPS request blocked (code: $HTTP_CODE)"
else
    fail "Non-allowlisted HTTPS request not blocked (code: $HTTP_CODE)"
fi

# --- Test 3: DNS allowlisted domain ---
info "Test 3: DNS resolution for allowlisted domain..."
if run_agent "test-dns-allow" debian:bookworm-slim \
    bash -c "apt-get update -qq && apt-get install -y -qq dnsutils >/dev/null 2>&1 && nslookup api.github.com $PROXY_IP >/dev/null 2>&1"; then
    pass "DNS resolution for allowlisted domain succeeded"
else
    fail "DNS resolution for allowlisted domain failed"
fi

# --- Test 4: DNS non-allowlisted domain (NXDOMAIN) ---
info "Test 4: DNS resolution for non-allowlisted domain..."
if run_agent "test-dns-deny" debian:bookworm-slim \
    bash -c "apt-get update -qq && apt-get install -y -qq dnsutils >/dev/null 2>&1 && nslookup example.com $PROXY_IP >/dev/null 2>&1"; then
    fail "DNS resolution for non-allowlisted domain should have failed"
else
    pass "DNS for non-allowlisted domain returned NXDOMAIN"
fi

# --- Test 5: Node.js DNS bypass path ---
info "Test 5: Node.js native fetch to non-allowlisted domain..."
if run_agent "test-node-bypass" node:22-slim \
    node -e "fetch('https://blocked.example.com').then(r => { console.log(r.status); process.exit(0); }).catch(e => { console.error(e.cause?.code || e.message); process.exit(1); })" 2>/dev/null; then
    fail "Node.js fetch to non-allowlisted domain should have failed"
else
    pass "Node.js fetch blocked for non-allowlisted domain (DNS-level)"
fi

# --- Test 6: Direct connection attempt (no proxy) ---
info "Test 6: Direct TCP connection attempt (bypassing proxy)..."
if run_agent "test-direct" debian:bookworm-slim \
    bash -c "timeout 5 bash -c 'echo | nc -w 3 93.184.216.34 80' 2>/dev/null"; then
    fail "Direct TCP connection should have failed on internal network"
else
    pass "Direct TCP connection blocked (no internet route on internal network)"
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
