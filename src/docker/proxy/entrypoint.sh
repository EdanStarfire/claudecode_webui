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

# --- Set up TPROXY transparent interception ---
# Agent containers share this container's network namespace via
# --network container:<proxy>. All TCP 80/443 originating from any process
# whose UID != 9999 (i.e. every non-mitmdump process) is intercepted.
#
# How it works:
#   OUTPUT chain:  mark packets from non-mitmdump processes (UID != 9999)
#                  with fwmark 1.
#   Policy routing: fwmark 1 → routing table 100; table 100 routes everything
#                  to loopback, re-injecting packets into the netfilter
#                  PREROUTING hook as if they arrived from outside.
#   PREROUTING:    TPROXY target on port 80/443 redirects to mitmdump :8080
#                  while preserving the original destination address via the
#                  IP_TRANSPARENT socket option.
#   Loop prevention: mitmdump runs as uid 9999; the OUTPUT MARK rule has
#                  ! --uid-owner 9999, so mitmdump's upstream connections
#                  to the real internet are never re-marked and never looped.
# PREROUTING: intercept TCP 80/443 arriving from outside this namespace.
# OUTPUT: intercept TCP 80/443 generated locally (agent processes sharing
#         this network namespace via --network container:<proxy>).
# uid 9999 (mitmdump) is excluded from OUTPUT REDIRECT so its upstream
# connections to the real internet are never re-intercepted (loop prevention).
# mitmproxy --mode transparent uses SO_ORIGINAL_DST to recover the original
# destination after REDIRECT. No policy routing or IP_TRANSPARENT needed.
echo "Configuring transparent proxy iptables..."
iptables -t nat -A PREROUTING -p tcp --dport 80 -j REDIRECT --to-port 8080
iptables -t nat -A PREROUTING -p tcp --dport 443 -j REDIRECT --to-port 8080
iptables -t nat -A OUTPUT -p tcp --dport 80 \
    -m owner ! --uid-owner 9999 -j REDIRECT --to-port 8080
iptables -t nat -A OUTPUT -p tcp --dport 443 \
    -m owner ! --uid-owner 9999 -j REDIRECT --to-port 8080

# --- Default-deny TCP OUTPUT ---
# Block all TCP from non-proxy processes on ports other than 80/443, closing
# the 2B gap (raw IP + non-HTTP/HTTPS was previously unrestricted).
#
# Rule order:
#   lo ACCEPT (all protocols): agent TCP port 80/443 is REDIRECT-ed to
#     127.0.0.1:8080 by nat OUTPUT before filter runs, so the output
#     interface becomes lo. This rule catches all redirected agent traffic
#     and all mitmdump→agent reply traffic.
#   ! -o lo dport 80 ACCEPT: mitmdump upstream HTTP connections.
#     Safe: agent port 80 is always redirected to lo first, so agent traffic
#     never reaches this rule. Only mitmdump (excluded from nat REDIRECT via
#     ! --uid-owner 9999) sends port 80 on the bridge interface.
#   ! -o lo dport 443 ACCEPT: same as above for HTTPS.
#   ! -o lo dport 53 uid 9998 ACCEPT: CoreDNS TCP upstream (DNS-over-TCP
#     fallback to 8.8.8.8). CoreDNS port 53 TCP goes to the bridge interface
#     (not lo). uid-owner 9998 match works for CoreDNS (verified by UDP counts).
#   tcp DROP: everything else (agent TCP to port 22, arbitrary ports, etc.).
#
# Note: -m owner --uid-owner 9999 is intentionally NOT used here because
# iptables owner match does not reliably match PID 1 connections created by
# exec setpriv; using ! -o lo + dport-based rules achieves the same security
# guarantee without relying on uid matching for mitmproxy.
echo "Configuring default-deny TCP OUTPUT..."
iptables -A OUTPUT -o lo -j ACCEPT
iptables -A OUTPUT ! -o lo -p tcp --dport 80 -j ACCEPT
iptables -A OUTPUT ! -o lo -p tcp --dport 443 -j ACCEPT
iptables -A OUTPUT ! -o lo -p tcp --dport 53 -m owner --uid-owner 9998 -j ACCEPT
iptables -A OUTPUT -p tcp -j DROP

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
# Running as uid 9999 is what the OUTPUT MARK rule excludes, preventing
# mitmdump's own upstream connections from being re-intercepted.
echo "Starting mitmdump (transparent mode) on :8080..."
echo "Addon: /etc/proxy/addon.py"
exec setpriv --reuid=9999 --regid=9999 --clear-groups -- \
    mitmdump --mode transparent --showhost -v \
    --set confdir="$CERTS_DIR" \
    --listen-port 8080 \
    --ssl-insecure \
    -s /etc/proxy/addon.py \
    --set block_global=false \
    --set stream_large_bodies=1m
