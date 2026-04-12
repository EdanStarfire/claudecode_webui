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
    --sysctl net.ipv4.conf.all.rp_filter=0 \
    --sysctl net.ipv4.conf.lo.rp_filter=0 \
    -v "$CERTS_DIR:/var/lib/mitmproxy" \
    "$PROXY_IMAGE"

# Wait for proxy to initialize
info "Waiting for proxy to start..."
sleep 5

info "Proxy startup logs:"
docker logs "$PROXY_CONTAINER" 2>&1

info "Proxy process list:"
docker exec "$PROXY_CONTAINER" ps aux 2>/dev/null || true

info "Listening sockets in proxy namespace:"
docker exec "$PROXY_CONTAINER" ss -tlunp 2>/dev/null || true

info "rp_filter values (must be 0 for OUTPUT REDIRECT):"
docker exec "$PROXY_CONTAINER" sh -c 'echo "  all=$(cat /proc/sys/net/ipv4/conf/all/rp_filter) lo=$(cat /proc/sys/net/ipv4/conf/lo/rp_filter) eth0=$(cat /proc/sys/net/ipv4/conf/eth0/rp_filter 2>/dev/null || echo N/A)"' 2>/dev/null || true

info "Mitmdump uid check:"
docker exec "$PROXY_CONTAINER" sh -c '
  for pid in /proc/[0-9]*; do
    cmd=$(cat "$pid/cmdline" 2>/dev/null | tr "\0" " ")
    case "$cmd" in
      *mitmdump*)
        echo "Found mitmdump PID ${pid##*/}:"
        grep -E "^(Name|Uid)" "$pid/status" 2>/dev/null
        break
        ;;
    esac
  done || echo "mitmdump not found in /proc"
'

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

# --- Diagnostics: test REDIRECT + uid-owner from INSIDE the proxy container ---
# These run curl from the proxy container itself (no agent container involved)
# to isolate whether the issue is REDIRECT/mitmproxy or --network container: sharing.
echo ""
info "Diag A: curl as root (uid 0) from proxy container → should be REDIRECT-ed to mitmproxy..."
DIAG_A=$(docker exec "$PROXY_CONTAINER" \
    curl -s --max-time 8 -o /dev/null -w "HTTP_%{http_code}" http://api.github.com/ 2>&1 || true)
info "  Result: ${DIAG_A:-<no output>}"

info "Diag B: curl as uid 9999 from proxy container → should BYPASS REDIRECT, go direct..."
DIAG_B=$(docker exec "$PROXY_CONTAINER" \
    setpriv --reuid=9999 --regid=9999 --clear-groups -- \
    curl -s --max-time 8 -o /dev/null -w "HTTP_%{http_code}" http://api.github.com/ 2>&1 || true)
info "  Result: ${DIAG_B:-<no output>}"

info "Diag C: curl as uid 0 to raw IP :80 from proxy container → should be REDIRECT-ed, addon 403..."
DIAG_C=$(docker exec "$PROXY_CONTAINER" \
    curl -s --max-time 8 -o /dev/null -w "HTTP_%{http_code}" http://93.184.216.34/ 2>&1 || true)
info "  Result: ${DIAG_C:-<no output>}"

info "Diag D: agent container wget with verbose stderr → shows connection/TLS details..."
DIAG_D=$(run_agent "test-diag" alpine \
    "wget -T 8 -O /dev/null http://api.github.com/ 2>&1 | head -5" 2>&1 || true)
info "  Result:"
echo "$DIAG_D" | head -10

info "Diag E: SO_ORIGINAL_DST test — does OUTPUT REDIRECT store original dest in conntrack?"
docker exec "$PROXY_CONTAINER" python3 -c "
import socket, struct, sys, threading, time

SO_ORIGINAL_DST = 80  # SOL_IP=0, SO_ORIGINAL_DST=80

result = []
def server():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(('0.0.0.0', 18999))
    s.listen(1)
    conn, addr = s.accept()
    try:
        dst = conn.getsockopt(socket.SOL_IP, SO_ORIGINAL_DST, 16)
        port = struct.unpack('!H', dst[2:4])[0]
        ip = '.'.join(str(b) for b in dst[4:8])
        result.append(f'SO_ORIGINAL_DST={ip}:{port}')
    except Exception as e:
        result.append(f'SO_ORIGINAL_DST error: {e}')
    conn.close()
    s.close()

t = threading.Thread(target=server, daemon=True)
t.start()
time.sleep(0.2)

# Temp REDIRECT: port 18080 → 18999
import subprocess
subprocess.run(['iptables','-t','nat','-A','OUTPUT','-p','tcp','--dport','18080','-j','REDIRECT','--to-port','18999'], check=True)
try:
    c = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    c.settimeout(3)
    c.connect(('127.0.0.1', 18080))  # goes to 18999 via REDIRECT
    c.close()
except Exception as e:
    result.append(f'connect error: {e}')
finally:
    subprocess.run(['iptables','-t','nat','-D','OUTPUT','-p','tcp','--dport','18080','-j','REDIRECT','--to-port','18999'])

t.join(timeout=3)
print(result[0] if result else 'no result')
" 2>&1 || true

echo ""

# --- Probe: direct TCP to mitmproxy :8080 (no REDIRECT needed) ---
# Verifies mitmdump is reachable at all before any iptables rules are involved.
# An HTTP request to the proxy port directly should return a 400 (bad request /
# upstream connection error) — anything other than a connection refusal means
# mitmdump is up and accepting connections.
info "Probe: direct HTTP to mitmdump :8080 (bypasses iptables)..."
DIRECT_RESP=$(run_agent "test-probe" alpine \
    "wget -qO- --timeout=5 http://127.0.0.1:8080/ 2>&1 | head -3" 2>/dev/null || true)
info "  Direct :8080 response: ${DIRECT_RESP:-<no output>}"

# --- Test 1a: Allowlisted domain (HTTP, transparent interception) ---
# Isolates REDIRECT firing from TLS/CA trust. HTTP goes through REDIRECT to
# mitmdump :8080 which then forwards upstream. If this passes but 1b fails,
# the issue is TLS certificate trust, not REDIRECT.
info "Test 1a: HTTP (port 80) request to allowlisted domain (api.github.com) — transparent..."
if run_agent "test-allow-http" alpine \
    "timeout 15 wget -T 10 -qO- http://api.github.com/ >/dev/null" \
    >/dev/null 2>&1; then
    pass "Allowlisted HTTP request succeeded (transparently intercepted)"
else
    fail "Allowlisted HTTP request failed (REDIRECT may not be firing)"
fi

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

# --- iptables packet counters (post-test) ---
# Non-zero counters on nat OUTPUT REDIRECT rules confirm REDIRECT is firing.
# Zero counters after tests = REDIRECT never matched (likely -m owner issue).
echo ""
echo "=== iptables nat table (post-test) ==="
docker exec "$PROXY_CONTAINER" iptables -t nat -L -v -n 2>/dev/null || true
echo ""
echo "=== iptables filter table (post-test) ==="
docker exec "$PROXY_CONTAINER" iptables -L -v -n 2>/dev/null || true
echo ""
echo "=== kernel log — dropped packets ==="
docker exec "$PROXY_CONTAINER" dmesg 2>/dev/null | grep -E "TCP-DROP|UDP-DROP" | tail -30 || echo "(no dmesg access or no matches)"
echo "========================================"

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
