#!/usr/bin/env bash
set -euo pipefail

CERTS_DIR="/var/lib/mitmproxy"
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
# Run as uid 9998 so the UDP default-deny OUTPUT rule can exempt it.
# cap_net_bind_service is set on the binary via setcap (Dockerfile), allowing
# it to bind :53 without root.
echo "Starting CoreDNS on :53..."
setpriv --reuid=9998 --regid=9998 --clear-groups -- \
    coredns -conf "$COREFILE" -dns.port 53 &
COREDNS_PID=$!

# --- Ensure cert directory is writable by mitmproxy uid ---
# Volume bind mounts override Dockerfile ownership; re-apply here as root
# before dropping privileges to uid 9999 for cert generation.
chown 9999:9999 "$CERTS_DIR"

# --- Generate CA cert if not present ---
if [ ! -f "$CERTS_DIR/mitmproxy-ca-cert.pem" ]; then
    echo "Generating mitmproxy CA certificate..."
    setpriv --reuid=9999 --regid=9999 --clear-groups -- \
        mitmdump --set confdir="$CERTS_DIR" -q &
    MITM_INIT_PID=$!
    sleep 2
    kill "$MITM_INIT_PID" 2>/dev/null || true
    wait "$MITM_INIT_PID" 2>/dev/null || true
    # Public cert must be readable by the host user for volume-mounting into agents.
    # Private key (mitmproxy-ca.pem) stays 600, owned by uid 9999.
    chmod 644 "$CERTS_DIR/mitmproxy-ca-cert.pem" 2>/dev/null || true
    echo "CA certificate generated at $CERTS_DIR/mitmproxy-ca-cert.pem"
else
    echo "Using existing CA certificate"
fi

# --- Set up NAT transparent interception ---
# Agent containers share this container's network namespace via
# --network container:<proxy>. All TCP 80/443 originating from any process
# whose UID != 9999 (i.e. every non-mitmdump process) is intercepted.
#
# How it works (nat OUTPUT DNAT to the container's own eth0 IP):
#
#   mitmproxy's transparent mode reads the original destination via SO_ORIGINAL_DST,
#   which requires a conntrack entry created by NAT in the OUTPUT chain. Two
#   previous approaches failed:
#     - REDIRECT to 127.0.0.1: the kernel implicitly changes the source address
#       to 127.0.0.1 for loopback-bound traffic, so the conntrack lookup by
#       SO_ORIGINAL_DST can't find the entry (wrong source key).
#     - MARK + policy routing + PREROUTING REDIRECT: conntrack already has an
#       entry for the flow from the OUTPUT path; re-injected packets arrive at
#       PREROUTING as ESTABLISHED and the nat table is skipped entirely.
#
#   The fix: DNAT to the container's own eth0 IP (not 127.0.0.1).
#   1. nat OUTPUT DNAT changes dst to $CONTAINER_IP:8080.
#   2. ip_route_me_harder() (called inside the nat OUTPUT hook) detects that
#      the new destination is a local address and re-routes to local delivery
#      BEFORE filter OUTPUT runs. Because $CONTAINER_IP is the eth0 address
#      (not a loopback address), the kernel routes via eth0, so filter OUTPUT
#      sees -o eth0 (not -o lo). A dedicated filter rule allows these packets.
#   3. Since the destination is a non-loopback local IP, the kernel does NOT
#      change the source address. The original source (e.g. 172.19.0.2:SP)
#      is preserved.
#   4. mitmproxy accepts at $CONTAINER_IP:8080.
#   5. SO_ORIGINAL_DST: conntrack finds the entry (source matches) and returns
#      the pre-DNAT destination (e.g. 140.82.113.6:80). ✓
#   Loop prevention: mitmdump runs as uid 9999; the DNAT rule excludes uid 9999
#      so mitmdump's upstream connections go directly to the internet.

# Determine the container's primary outbound IP (used as DNAT destination to
# avoid loopback source-address rewriting).
CONTAINER_IP=$(ip route get 8.8.8.8 2>/dev/null | awk 'NR==1 {for(i=1;i<=NF;i++) if ($i=="src") {print $(i+1); exit}}')
if [ -z "$CONTAINER_IP" ]; then
    CONTAINER_IP=$(ip -4 addr show eth0 | awk '/inet / {gsub(/\/.*/, "", $2); print $2; exit}')
fi
echo "Container IP for DNAT: $CONTAINER_IP"

echo "Configuring NAT transparent proxy (OUTPUT DNAT → $CONTAINER_IP:8080)..."

# DNAT agent TCP 80/443 to mitmdump. uid 9999 (mitmdump) is excluded to
# prevent re-interception of mitmdump's own upstream connections.
iptables -t nat -A OUTPUT -p tcp --dport 80 \
    -m owner ! --uid-owner 9999 -j DNAT --to-destination "$CONTAINER_IP:8080"
iptables -t nat -A OUTPUT -p tcp --dport 443 \
    -m owner ! --uid-owner 9999 -j DNAT --to-destination "$CONTAINER_IP:8080"

# --- Default-deny TCP OUTPUT ---
# Block all TCP from non-proxy processes on ports other than 80/443, closing
# the raw-IP gap (non-HTTP/HTTPS TCP was previously unrestricted).
#
# Rule order:
#   1. lo ACCEPT: loopback traffic (inter-process comms, CoreDNS responses).
#   2. DNAT'd HTTP/HTTPS ACCEPT: nat OUTPUT DNAT changes dst to $CONTAINER_IP:8080.
#      ip_route_me_harder() re-routes to local delivery via eth0 (because
#      $CONTAINER_IP is an eth0 address, not a loopback address), so filter
#      OUTPUT sees -o eth0 for intercepted packets. This rule allows them through.
#   3. ESTABLISHED,RELATED ACCEPT: TCP handshake completion, return traffic.
#   4. uid 9999 NEW ACCEPT: mitmdump upstream connections to the real internet.
#      Works because mitmdump is a child process (not PID 1); xt_owner uid
#      matching is reliable for non-PID-1 processes.
#   5. tcp NEW DROP: everything else (agent TCP to port 22, 8443, etc.).
echo "Configuring default-deny TCP OUTPUT..."

# 1. Allow all loopback traffic (inter-process comms).
iptables -A OUTPUT -o lo -j ACCEPT

# 2. Allow DNAT'd agent HTTP/HTTPS traffic to reach mitmproxy at $CONTAINER_IP:8080.
#    After nat OUTPUT DNAT, ip_route_me_harder() routes these via eth0 (not lo)
#    because $CONTAINER_IP is an eth0 address. The -o lo rule above does not
#    match; this rule fills the gap so they reach mitmdump.
iptables -A OUTPUT -p tcp -d "$CONTAINER_IP" --dport 8080 \
    -m conntrack --ctstate NEW -j ACCEPT
# Allow connections to the regular HTTP proxy port (8888) and SOCKS5 port (1080).
# These are used by agents on a separate Docker network that connect via explicit
# proxy env vars (HTTPS_PROXY=http://...@proxy:8888, ALL_PROXY=socks5://...@proxy:1080)
# rather than sharing this container's network namespace.
iptables -A OUTPUT -p tcp -d "$CONTAINER_IP" --dport 8888 \
    -m conntrack --ctstate NEW -j ACCEPT
iptables -A OUTPUT -p tcp -d "$CONTAINER_IP" --dport 1080 \
    -m conntrack --ctstate NEW -j ACCEPT

# 3. Allow established/related traffic (TCP handshake completion, return traffic).
iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT

# 4. Allow mitmdump (uid 9999) to initiate new upstream connections.
iptables -A OUTPUT -p tcp -m owner --uid-owner 9999 -m conntrack --ctstate NEW -j ACCEPT

# 5. Drop all other new outbound TCP (port 22, 8443, arbitrary ports, etc.).
iptables -A OUTPUT -p tcp -m conntrack --ctstate NEW -j DROP

# --- Default-deny UDP OUTPUT ---
# Block all UDP from non-proxy processes. This prevents DNS exfiltration via
# direct UDP queries to external resolvers (e.g. nslookup 1.1.1.1) and any
# other raw-IP UDP protocol bypasses.
#
# uid 9998 (CoreDNS) exempted so it can forward UDP DNS queries upstream to
# 8.8.8.8. All other UDP (from agents or system) is dropped.
#
# To add future protocol proxies: insert uid ACCEPT before the DROP.
echo "Configuring default-deny UDP OUTPUT..."
iptables -A OUTPUT -p udp -m owner --uid-owner 9998 -j ACCEPT
iptables -A OUTPUT -p udp -j DROP

# --- Start mitmdump with multiple modes as uid 9999 ---
# setpriv drops to uid/gid 9999 but retains CAP_NET_ADMIN as an ambient
# capability so mitmproxy can use privileged socket options if needed.
#
# Three proxy modes run in a single mitmdump process (mitmproxy 10+ feature):
#   :8080 transparent — DNAT interception for containers sharing this namespace
#   :8888 regular     — explicit HTTP/HTTPS proxy (HTTPS_PROXY env var clients)
#   :1080 socks5      — explicit SOCKS5 proxy (ALL_PROXY env var, SSH ProxyCommand)
#
# All three modes share the same addon (domain allowlist filter) and CA cert.
#
# IMPORTANT: do NOT use exec here. The nat OUTPUT DNAT rule uses
# "! --uid-owner 9999" to exclude mitmproxy's own upstream connections
# from being re-intercepted. The Linux kernel's xt_owner module does not
# reliably match uid for PID 1; if mitmdump were exec'd into PID 1, its
# upstream connections would fail the uid match and be DNAT-ed back to
# itself (loop). Running as a child process (non-PID-1) ensures uid 9999
# is matched correctly by the owner module.
#
# PID 1 (this shell) remains as the container's init process and forwards
# SIGTERM/SIGINT to the child processes for clean shutdown.
echo "Starting mitmdump (transparent:8080, regular:8888, socks5:1080)..."
echo "Addon: /etc/proxy/addon.py"
PYTHONUNBUFFERED=1 setpriv --reuid=9999 --regid=9999 --clear-groups \
    --inh-caps +net_admin --ambient-caps +net_admin -- \
    mitmdump \
    --mode transparent \
    --mode regular@8888 \
    --mode socks5@1080 \
    --listen-port 8080 \
    --showhost -v \
    --set confdir="$CERTS_DIR" \
    --ssl-insecure \
    -s /etc/proxy/addon.py \
    --set block_global=false \
    --set stream_large_bodies=1m &
MITM_PID=$!

# Forward signals to child processes so Docker stop works cleanly.
trap 'kill "$MITM_PID" "$COREDNS_PID" 2>/dev/null; wait' TERM INT

echo "Proxy ready (coredns PID=$COREDNS_PID, mitmdump PID=$MITM_PID)"
wait "$MITM_PID"
