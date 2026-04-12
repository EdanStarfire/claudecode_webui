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

# --- Set up transparent interception via DNAT ---
# Agent containers share this container's network namespace via
# --network container:<proxy>. All TCP 80/443 originating from any process
# whose UID != 9999 (i.e. every non-mitmdump process) is intercepted.
#
# How it works:
#   PREROUTING: REDIRECT intercepts TCP 80/443 arriving from external
#               interfaces (e.g. if containers route through this namespace).
#   OUTPUT:     DNAT rewrites locally-generated TCP 80/443 to PROXY_IP:8080.
#               The kernel re-routes the packet to the local address, delivering
#               it to mitmdump's listening socket on :8080.
#   Loop prevention: mitmdump runs as uid 9999; the OUTPUT DNAT rule has
#               ! --uid-owner 9999, so mitmdump's upstream connections to the
#               real internet are never re-intercepted (no loop).
#   SO_ORIGINAL_DST: mitmproxy --mode transparent uses getsockopt(SOL_IP, 80)
#               to recover the original destination from conntrack. This works
#               identically with DNAT and REDIRECT.
#
# Why DNAT instead of REDIRECT?
#   REDIRECT in OUTPUT always rewrites the destination to 127.0.0.1, routing
#   the packet through lo. The packet arrives on lo with the container's bridge
#   IP as source (e.g. 172.18.0.2), which is NOT in 127.0.0.0/8. The Linux
#   kernel silently drops these packets — a check beyond rp_filter rejects
#   non-loopback sources on the lo interface.
#   DNAT to the container's own bridge IP avoids this: both source and
#   destination are the same local address, so the kernel accepts the packet.

# Detect the container's primary IP for DNAT target.
PROXY_IP=$(ip -4 addr show eth0 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}' || true)
if [ -z "$PROXY_IP" ]; then
    # Fallback: first non-loopback IPv4 address
    PROXY_IP=$(ip -4 addr show scope global 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | head -1 || true)
fi
if [ -z "$PROXY_IP" ]; then
    echo "ERROR: Could not detect container IP for DNAT. Transparent proxy will not work."
    PROXY_IP="127.0.0.1"  # Fallback (will likely fail, but avoids syntax error)
fi
echo "Container IP for DNAT: $PROXY_IP"

echo "Configuring transparent proxy iptables..."
# PREROUTING: intercept traffic arriving from external interfaces.
# REDIRECT is fine here — packets arrive on a real interface with matching source.
iptables -t nat -A PREROUTING -p tcp --dport 80 -j REDIRECT --to-port 8080
iptables -t nat -A PREROUTING -p tcp --dport 443 -j REDIRECT --to-port 8080
# OUTPUT: intercept locally-generated TCP 80/443 (agent processes).
# DNAT to the container's own IP avoids the lo routing issue with REDIRECT.
iptables -t nat -A OUTPUT -p tcp --dport 80 \
    -m owner ! --uid-owner 9999 -j DNAT --to-destination "${PROXY_IP}:8080"
iptables -t nat -A OUTPUT -p tcp --dport 443 \
    -m owner ! --uid-owner 9999 -j DNAT --to-destination "${PROXY_IP}:8080"

# --- Default-deny TCP OUTPUT ---
# Block all TCP from non-proxy processes on ports other than 80/443, closing
# the raw-IP gap (non-HTTP/HTTPS TCP was previously unrestricted).
#
# Rule order:
#   1. lo ACCEPT: covers DNAT-ed agent traffic (nat OUTPUT DNAT rewrites dst
#      to PROXY_IP:8080 → kernel recognizes local address → routes through
#      lo → filter sees -o lo → ACCEPT) and all inter-process loopback comms.
#   2. ESTABLISHED,RELATED ACCEPT: allows TCP handshakes to complete and
#      return traffic for all established connections.
#   3. uid 9999 NEW ACCEPT: mitmdump upstream connections to the real internet.
#      Works because mitmdump is a child process (not PID 1); xt_owner uid
#      matching is reliable for non-PID-1 processes.
#   4. dport 80,443 NEW ACCEPT: agent TCP to these ports must pass filter
#      before nat OUTPUT DNAT fires. Without this, they'd hit the DROP rule
#      and never reach the DNAT target.
#   5. tcp NEW DROP: everything else (agent TCP to port 22, 8443, etc.).
echo "Configuring default-deny TCP OUTPUT..."

# 1. Allow all loopback traffic (DNAT-ed agent traffic + inter-process comms).
iptables -A OUTPUT -o lo -j ACCEPT

# 2. Allow established/related traffic (TCP handshake completion, return traffic).
iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT

# 3. Allow mitmdump (uid 9999) to initiate new upstream connections.
iptables -A OUTPUT -p tcp -m owner --uid-owner 9999 -m conntrack --ctstate NEW -j ACCEPT

# 4. Allow agent NEW TCP to ports 80/443 so packets reach nat OUTPUT DNAT.
iptables -A OUTPUT -p tcp -m multiport --dports 80,443 -m conntrack --ctstate NEW -j ACCEPT

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

# --- Start mitmdump in transparent mode as uid 9999 ---
# setpriv drops to uid/gid 9999. CAP_NET_ADMIN is not needed for
# transparent mode (it uses SO_ORIGINAL_DST, not IP_TRANSPARENT).
#
# IMPORTANT: do NOT use exec here. The iptables nat OUTPUT DNAT rule
# uses "! --uid-owner 9999" to exclude mitmproxy's own upstream connections
# from being re-intercepted. The Linux kernel's xt_owner module does not
# reliably match uid for PID 1; if mitmdump were exec'd into PID 1, its
# upstream connections would fail the uid match and be DNAT-ed back to
# itself (loop). Running as a child process (non-PID-1) ensures uid 9999
# is matched correctly by the owner module.
#
# PID 1 (this shell) remains as the container's init process and forwards
# SIGTERM/SIGINT to the child processes for clean shutdown.
echo "Starting mitmdump (transparent mode) on :8080..."
echo "Addon: /etc/proxy/addon.py"
PYTHONUNBUFFERED=1 setpriv --reuid=9999 --regid=9999 --clear-groups -- \
    mitmdump --mode transparent --showhost -v \
    --set confdir="$CERTS_DIR" \
    --listen-port 8080 \
    --ssl-insecure \
    -s /etc/proxy/addon.py \
    --set block_global=false \
    --set stream_large_bodies=1m &
MITM_PID=$!

# Forward signals to child processes so Docker stop works cleanly.
trap 'kill "$MITM_PID" "$COREDNS_PID" 2>/dev/null; wait' TERM INT

echo "Proxy ready (coredns PID=$COREDNS_PID, mitmdump PID=$MITM_PID)"
wait "$MITM_PID"
