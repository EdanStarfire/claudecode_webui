#!/usr/bin/env bash
set -euo pipefail

# Configuration
PROXY_IMAGE="claude-proxy:local"
PROXY_CONTAINER="claude-proxy-test"
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
    docker network rm "$BRIDGE_NET" 2>/dev/null || true
    info "Cleanup complete"
}

trap cleanup EXIT

# --- Setup ---
info "Building proxy image..."
docker build -t "$PROXY_IMAGE" "$SCRIPT_DIR"

info "Creating networks..."
# bridge-net: gives the proxy container outbound internet access.
# Agent containers share the proxy's network namespace via
# --network container:<proxy>; they inherit all proxy interfaces and routing,
# but TPROXY iptables rules intercept their traffic before it leaves.
docker network create "$BRIDGE_NET" 2>/dev/null || true

info "Creating certs directory..."
mkdir -p "$CERTS_DIR"

info "Starting proxy container..."
docker run -d \
    --name "$PROXY_CONTAINER" \
    --network "$BRIDGE_NET" \
    --cap-add NET_ADMIN \
    -v "$CERTS_DIR:/var/lib/mitmproxy" \
    "$PROXY_IMAGE"

# Wait for proxy to initialize
info "Waiting for proxy to start..."
sleep 5
docker logs "$PROXY_CONTAINER" --tail 10

# Agent containers share the proxy's network namespace; CoreDNS listens on
# loopback :53, so 127.0.0.1 resolves allowlisted domains and blocks others.
PROXY_IP="127.0.0.1"

# Verify CA cert was generated
if [ -f "$CERTS_DIR/mitmproxy-ca-cert.pem" ]; then
    pass "CA certificate generated at $CERTS_DIR/mitmproxy-ca-cert.pem"
else
    fail "CA certificate not found"
    exit 1
fi

# --- Helper: run test container sharing the proxy's network namespace ---
# --network container:<proxy> gives the agent the same network interfaces,
# iptables rules, and loopback as the proxy. nat OUTPUT REDIRECT intercepts
# all TCP 80/443 from non-uid-9999 processes; no capability or route
# configuration needed in the agent container. DNS is set by pointing
# resolv.conf at 127.0.0.1 where CoreDNS is listening
# (--dns is not usable with --network container:).
run_agent() {
    local name="$1"
    local image="$2"
    local cmd="$3"
    docker run --rm \
        --name test-agent \
        --network "container:$PROXY_CONTAINER" \
        -e "NODE_EXTRA_CA_CERTS=/etc/proxy-ca/mitmproxy-ca-cert.pem" \
        -v "$CERTS_DIR/mitmproxy-ca-cert.pem:/etc/proxy-ca/mitmproxy-ca-cert.pem:ro" \
        --entrypoint sh \
        "$image" \
        -c "echo 'nameserver 127.0.0.1' > /etc/resolv.conf; cat /etc/proxy-ca/mitmproxy-ca-cert.pem >> /etc/ssl/certs/ca-certificates.crt 2>/dev/null || true; $cmd"
}

# --- Test 1: Allowlisted domain (HTTPS, transparent interception) ---
# No proxy env vars. Traffic routes through proxy via iptables REDIRECT.
# mitmdump intercepts TLS, presents mitmproxy CA-signed cert; wget trusts it.
info "Test 1: HTTPS request to allowlisted domain (api.github.com) — transparent..."
if run_agent "test-allow" alpine \
    "timeout 15 wget -T 10 -qO- https://api.github.com/ >/dev/null" \
    >/dev/null 2>&1; then
    pass "Allowlisted HTTPS request succeeded (transparently intercepted)"
else
    fail "Allowlisted HTTPS request failed"
fi

# --- Test 2: Non-allowlisted domain (HTTPS, transparent interception) ---
# DNS returns NXDOMAIN for example.com (CoreDNS catch-all block).
info "Test 2: HTTPS request to non-allowlisted domain (example.com) — transparent..."
if run_agent "test-deny" alpine \
    "timeout 15 wget -T 10 -qO- https://example.com/ >/dev/null 2>&1"; then
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

# --- Test 6: Direct HTTP to raw IP (bypass DNS, test OUTPUT REDIRECT) ---
# Sends an HTTP GET to example.com's IP (93.184.216.34) on port 80, bypassing
# DNS entirely. The nat OUTPUT REDIRECT rule catches port 80 and redirects to
# mitmdump :8080. The raw IP does not match any allowlisted domain so the
# addon returns HTTP 403. wget treats 4xx as an error and exits non-zero.
info "Test 6: Direct HTTP to raw IP — OUTPUT REDIRECT interception and block..."
if run_agent "test-direct" alpine \
    "timeout 15 wget -T 10 -qO- http://93.184.216.34/ >/dev/null 2>&1" \
    >/dev/null 2>&1; then
    fail "Direct HTTP to raw IP should have been intercepted and blocked (403)"
else
    pass "Direct HTTP to raw IP blocked (TPROXY intercepted, addon returned 403)"
fi

# --- Test 7: SSH TCP to allowlisted domain (port 22, blocked by default-deny) ---
# DNS for github.com resolves (it is allowlisted), but the default-deny OUTPUT
# rule drops all TCP that is not port 80/443 or from uid 9999. Port 22 never
# reaches the wire regardless of whether the domain is allowlisted.
info "Test 7: SSH TCP to allowlisted domain (github.com:22) — blocked by default-deny OUTPUT..."
if run_agent "test-ssh" alpine \
    "timeout 5 nc -w 3 github.com 22 </dev/null 2>/dev/null" \
    >/dev/null 2>&1; then
    fail "SSH to allowlisted domain should be blocked by default-deny OUTPUT"
else
    pass "SSH to allowlisted domain blocked (default-deny OUTPUT, port 22 not permitted)"
fi

# --- Test 8: SSH TCP to non-allowlisted domain (DNS blocks it) ---
# DNS NXDOMAIN for example.com prevents nc from resolving the host; the
# default-deny TCP DROP would also block it if DNS somehow resolved.
info "Test 8: SSH TCP connect to non-allowlisted domain (example.com:22) — DNS blocked..."
if run_agent "test-ssh-deny" alpine \
    "timeout 5 nc -w 3 example.com 22 </dev/null 2>/dev/null" \
    >/dev/null 2>&1; then
    fail "SSH to non-allowlisted domain should have failed (DNS NXDOMAIN)"
else
    pass "SSH to non-allowlisted domain blocked (DNS NXDOMAIN)"
fi

# --- Test 9: UDP to external resolver (DNS exfiltration path, now blocked) ---
# nslookup with an explicit server address sends UDP directly to 1.1.1.1:53,
# bypassing CoreDNS entirely. The default-deny UDP OUTPUT DROP blocks it;
# the query times out and nslookup exits non-zero.
# This closes the 2B gap for UDP: raw IP + non-HTTP/HTTPS protocol was
# previously unrestricted; default-deny now covers both TCP and UDP.
info "Test 9: UDP DNS query to external resolver (1.1.1.1) — blocked by default-deny UDP..."
if run_agent "test-udp-exfil" alpine \
    "nslookup api.github.com 1.1.1.1 >/dev/null 2>&1" \
    >/dev/null 2>&1; then
    fail "Direct UDP to external resolver should be blocked by default-deny"
else
    pass "UDP to external resolver blocked (default-deny UDP OUTPUT)"
fi

# --- Proxy traffic log ---
echo ""
echo "=== Proxy container logs ==="
docker logs "$PROXY_CONTAINER" 2>&1
echo "============================"

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
