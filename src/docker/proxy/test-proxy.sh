#!/usr/bin/env bash
set -euo pipefail

# Usage: test-proxy.sh [-v|--verbose]
#   -v / --verbose  Show startup state, per-rule diagnostics, and post-test
#                   iptables counters. Useful when a test fails unexpectedly.

PROXY_IMAGE="claude-proxy:local"
PROXY_CONTAINER="claude-proxy-test"
BRIDGE_NET="bridge-net-test"
CERTS_DIR="$(cd "$(dirname "$0")" && pwd)/certs"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VERBOSE=0

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { echo -e "${GREEN}PASS${NC}: $1"; }
fail() { echo -e "${RED}FAIL${NC}: $1"; FAILURES=$((FAILURES + 1)); }
info() { echo -e "${YELLOW}INFO${NC}: $1"; }

FAILURES=0

for arg in "$@"; do
    case "$arg" in
        -v|--verbose) VERBOSE=1 ;;
        -h|--help) echo "Usage: $0 [-v|--verbose]"; exit 0 ;;
        *) echo "Unknown argument: $arg" >&2; exit 1 ;;
    esac
done

cleanup() {
    docker rm -f "$PROXY_CONTAINER" 2>/dev/null || true
    docker rm -f test-agent 2>/dev/null || true
    docker network rm "$BRIDGE_NET" 2>/dev/null || true
}

trap cleanup EXIT

# --- Setup ---
info "Building proxy image..."
if [ "$VERBOSE" -eq 1 ]; then
    docker build -t "$PROXY_IMAGE" "$SCRIPT_DIR"
else
    docker build -q -t "$PROXY_IMAGE" "$SCRIPT_DIR" >/dev/null
fi

docker network create "$BRIDGE_NET" 2>/dev/null || true
mkdir -p "$CERTS_DIR"

info "Starting proxy container..."
docker run -d \
    --name "$PROXY_CONTAINER" \
    --network "$BRIDGE_NET" \
    --cap-add NET_ADMIN \
    --sysctl net.ipv4.ip_forward=1 \
    --sysctl net.ipv4.conf.lo.accept_local=1 \
    -v "$CERTS_DIR:/var/lib/mitmproxy" \
    "$PROXY_IMAGE" >/dev/null

info "Waiting for proxy to start..."
sleep 5

if [ "$VERBOSE" -eq 1 ]; then
    echo ""
    info "Proxy startup logs:"
    docker logs "$PROXY_CONTAINER" 2>&1
    echo ""
    info "Proxy process list:"
    docker exec "$PROXY_CONTAINER" ps aux 2>/dev/null || true
    info "Listening sockets:"
    docker exec "$PROXY_CONTAINER" ss -tlunp 2>/dev/null || true
    info "Mitmdump uid:"
    docker exec "$PROXY_CONTAINER" sh -c '
      for pid in /proc/[0-9]*; do
        cmd=$(cat "$pid/cmdline" 2>/dev/null | tr "\0" " ")
        case "$cmd" in
          *mitmdump*)
            grep -E "^(Name|Uid)" "$pid/status" 2>/dev/null
            break;;
        esac
      done' 2>/dev/null || true
    echo ""
fi

# Agent containers share the proxy's network namespace; CoreDNS listens on
# loopback :53, so 127.0.0.1 resolves allowlisted domains and returns NXDOMAIN
# for everything else.
PROXY_IP="127.0.0.1"

# Verify CA cert was generated
if [ -f "$CERTS_DIR/mitmproxy-ca-cert.pem" ]; then
    pass "CA certificate generated at $CERTS_DIR/mitmproxy-ca-cert.pem"
else
    fail "CA certificate not found"
    info "Proxy logs:"
    docker logs "$PROXY_CONTAINER" 2>&1
    exit 1
fi

# --- Helper: run a test container sharing the proxy's network namespace ---
# --network container:<proxy> gives the agent the same interfaces, iptables
# rules, and routing as the proxy. nat OUTPUT DNAT redirects agent TCP 80/443
# to the proxy's own eth0 IP:8080; mitmproxy reads the pre-DNAT destination via
# SO_ORIGINAL_DST. No extra capabilities needed in the agent container.
# DNS is set by pointing resolv.conf at 127.0.0.1 (CoreDNS); --dns is not
# usable with --network container:.
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

# --- Verbose diagnostics ---
# Run before the main tests to confirm interception is working from both
# inside the proxy container and from an agent container.
if [ "$VERBOSE" -eq 1 ]; then
    echo ""
    info "Diag A: curl as root (uid 0) from proxy container → DNAT → mitmproxy..."
    DIAG_A=$(docker exec "$PROXY_CONTAINER" \
        curl -s --max-time 8 -o /dev/null -w "HTTP_%{http_code}" http://api.github.com/ 2>&1 || true)
    info "  Result: ${DIAG_A:-<no output>}"

    info "Diag B: curl as uid 9999 from proxy container → bypasses DNAT, goes direct..."
    DIAG_B=$(docker exec "$PROXY_CONTAINER" \
        setpriv --reuid=9999 --regid=9999 --clear-groups -- \
        curl -s --max-time 8 -o /dev/null -w "HTTP_%{http_code}" http://api.github.com/ 2>&1 || true)
    info "  Result: ${DIAG_B:-<no output>}"

    info "Diag C: curl as root to raw IP :80 from proxy container → DNAT → addon 403..."
    DIAG_C=$(docker exec "$PROXY_CONTAINER" \
        curl -s --max-time 8 -o /dev/null -w "HTTP_%{http_code}" http://93.184.216.34/ 2>&1 || true)
    info "  Result: ${DIAG_C:-<no output>}"

    info "Diag D: agent container wget → shows connection/TLS details..."
    DIAG_D=$(run_agent "test-diag" alpine \
        "wget -T 8 -O /dev/null http://api.github.com/ 2>&1 | head -5" 2>&1 || true)
    info "  Result:"
    echo "$DIAG_D" | head -10
    echo ""
fi

# --- Tests ---

# Test 1: Allowlisted domain (HTTP)
# Confirms DNAT is firing and the addon allows the domain. If this passes
# but Test 2 fails, the issue is TLS certificate trust, not interception.
info "Test 1: HTTP request to allowlisted domain (api.github.com) — transparent..."
if run_agent "test-allow-http" alpine \
    "timeout 15 wget -T 10 -qO- http://api.github.com/ >/dev/null" \
    >/dev/null 2>&1; then
    pass "Allowlisted HTTP request succeeded (transparently intercepted)"
else
    fail "Allowlisted HTTP request failed"
fi

# Test 2: Allowlisted domain (HTTPS)
# No proxy env vars. DNAT intercepts TCP 443; mitmdump terminates TLS and
# presents a CA-signed cert; wget trusts it via the mounted CA bundle.
info "Test 2: HTTPS request to allowlisted domain (api.github.com) — transparent..."
if run_agent "test-allow" alpine \
    "timeout 15 wget -T 10 -qO- https://api.github.com/ >/dev/null" \
    >/dev/null 2>&1; then
    pass "Allowlisted HTTPS request succeeded (transparently intercepted)"
else
    fail "Allowlisted HTTPS request failed"
fi

# Test 3: Non-allowlisted domain (HTTPS)
# CoreDNS returns NXDOMAIN for example.com; wget cannot connect.
info "Test 3: HTTPS request to non-allowlisted domain (example.com) — should be blocked..."
if run_agent "test-deny" alpine \
    "timeout 15 wget -T 10 -qO- https://example.com/ >/dev/null 2>&1"; then
    fail "Non-allowlisted HTTPS request should have been blocked"
else
    pass "Non-allowlisted HTTPS request blocked (DNS NXDOMAIN)"
fi

# Test 4: DNS — allowlisted domain resolves
info "Test 4: DNS resolution for allowlisted domain..."
if run_agent "test-dns-allow" alpine \
    "nslookup api.github.com $PROXY_IP >/dev/null 2>&1" \
    >/dev/null 2>&1; then
    pass "DNS resolution for allowlisted domain succeeded"
else
    fail "DNS resolution for allowlisted domain failed"
fi

# Test 5: DNS — non-allowlisted domain returns NXDOMAIN
info "Test 5: DNS resolution for non-allowlisted domain — should return NXDOMAIN..."
if run_agent "test-dns-deny" alpine \
    "nslookup example.com $PROXY_IP >/dev/null 2>&1" \
    >/dev/null 2>&1; then
    fail "DNS resolution for non-allowlisted domain should have failed"
else
    pass "DNS for non-allowlisted domain returned NXDOMAIN"
fi

# Test 6: Node.js native fetch to non-allowlisted domain
# DNS NXDOMAIN prevents resolution; verifies that Node's built-in fetch is
# also blocked (no http_proxy env var required).
info "Test 6: Node.js native fetch to non-allowlisted domain — should be blocked..."
if run_agent "test-node-bypass" node:22-slim \
    "node -e \"fetch('https://blocked.example.com').then(r => { console.log(r.status); process.exit(0); }).catch(e => { console.error(e.cause?.code || e.message); process.exit(1); })\"" \
    >/dev/null 2>&1; then
    fail "Node.js fetch to non-allowlisted domain should have failed"
else
    pass "Node.js fetch blocked for non-allowlisted domain (DNS-level)"
fi

# Test 7: Direct HTTP to raw IP (bypasses DNS)
# Sends HTTP to 93.184.216.34:80 without DNS. DNAT intercepts it; the raw IP
# does not match any allowlisted domain so the addon returns 403. wget treats
# 4xx as an error and exits non-zero.
info "Test 7: Direct HTTP to raw IP — should be intercepted and blocked (addon 403)..."
if run_agent "test-direct" alpine \
    "timeout 15 wget -T 10 -qO- http://93.184.216.34/ >/dev/null 2>&1" \
    >/dev/null 2>&1; then
    fail "Direct HTTP to raw IP should have been intercepted and blocked (403)"
else
    pass "Direct HTTP to raw IP blocked (DNAT intercepted, addon returned 403)"
fi

# Test 8: SSH TCP to allowlisted domain (port 22, default-deny)
# DNS for github.com resolves (it is allowlisted), but the default-deny OUTPUT
# rule drops all TCP that is not port 80/443 or from uid 9999.
info "Test 8: SSH TCP to allowlisted domain (github.com:22) — blocked by default-deny..."
if run_agent "test-ssh" alpine \
    "timeout 5 nc -w 3 github.com 22 </dev/null 2>/dev/null" \
    >/dev/null 2>&1; then
    fail "SSH to allowlisted domain should be blocked by default-deny OUTPUT"
else
    pass "SSH to allowlisted domain blocked (default-deny OUTPUT, port 22 not permitted)"
fi

# Test 9: SSH TCP to non-allowlisted domain (DNS + default-deny)
# DNS NXDOMAIN prevents nc from resolving example.com; default-deny would
# also block it if resolution somehow succeeded.
info "Test 9: SSH TCP to non-allowlisted domain (example.com:22) — should be blocked..."
if run_agent "test-ssh-deny" alpine \
    "timeout 5 nc -w 3 example.com 22 </dev/null 2>/dev/null" \
    >/dev/null 2>&1; then
    fail "SSH to non-allowlisted domain should have failed"
else
    pass "SSH to non-allowlisted domain blocked (DNS NXDOMAIN)"
fi

# Test 10: UDP to external resolver (DNS exfiltration path)
# nslookup with an explicit server address sends UDP directly to 1.1.1.1:53,
# bypassing CoreDNS. The default-deny UDP OUTPUT DROP blocks it.
info "Test 10: UDP DNS query to external resolver (1.1.1.1) — blocked by default-deny UDP..."
if run_agent "test-udp-exfil" alpine \
    "nslookup api.github.com 1.1.1.1 >/dev/null 2>&1" \
    >/dev/null 2>&1; then
    fail "Direct UDP to external resolver should be blocked by default-deny"
else
    pass "UDP to external resolver blocked (default-deny UDP OUTPUT)"
fi

# --- Verbose post-test dumps ---
if [ "$VERBOSE" -eq 1 ]; then
    echo ""
    echo "=== Proxy container logs ==="
    docker logs "$PROXY_CONTAINER" 2>&1
    echo ""
    echo "=== iptables nat table (post-test) ==="
    docker exec "$PROXY_CONTAINER" iptables -t nat -L -v -n 2>/dev/null || true
    echo ""
    echo "=== iptables filter table (post-test) ==="
    docker exec "$PROXY_CONTAINER" iptables -L -v -n 2>/dev/null || true
    echo "========================================"
fi

# --- Results ---
echo ""
if [ $FAILURES -eq 0 ]; then
    echo -e "${GREEN}All tests passed${NC}"
    exit 0
else
    echo -e "${RED}$FAILURES test(s) failed${NC}"
    exit 1
fi
