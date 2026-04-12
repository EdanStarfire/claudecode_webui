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
#   lo ACCEPT first: covers CoreDNS TCP fallback and TPROXY re-injection
#                    (marked 80/443 packets route to lo before filter runs).
#   uid 9999 ACCEPT: mitmdump upstream connections exit freely to internet.
#   dport 80/443 ACCEPT: agent HTTP/HTTPS — caught by TPROXY, never reach wire.
#   tcp DROP: everything else (port 22, raw-IP non-HTTP, etc.) is blocked.
#
# To add a future protocol proxy (sshpiper uid 9998, SOCKS uid 9997, etc.),
# insert an additional uid ACCEPT rule before the final DROP.
echo "Configuring default-deny TCP OUTPUT..."
# nat OUTPUT REDIRECT fires before filter OUTPUT. After REDIRECT rewrites the
# destination to 127.0.0.1:8080, the routing decision picks lo, so filter
# OUTPUT sees -o lo and ACCEPTs. The explicit dport 80/443 rules below are
# therefore redundant but kept as documentation of intent.
iptables -A OUTPUT -o lo -j ACCEPT
iptables -A OUTPUT -p tcp -m owner --uid-owner 9999 -j ACCEPT
iptables -A OUTPUT -p tcp --dport 80 -j ACCEPT
iptables -A OUTPUT -p tcp --dport 443 -j ACCEPT
iptables -A OUTPUT -p tcp -j DROP

# --- Default-deny UDP OUTPUT ---
# Same pattern as TCP: loopback always allowed, then per-service UID exemptions,
# then drop everything else. This blocks DNS exfiltration to external resolvers
# (e.g. nc/nslookup directly to 1.1.1.1:53) and any other UDP to raw IPs.
#
# uid 9998 (CoreDNS) must be exempted so it can forward DNS queries upstream
# to 8.8.8.8. uid 9999 (mitmdump) exempted for any internal UDP it may use.
#
# To add future protocol proxies: insert uid ACCEPT before the DROP.
echo "Configuring default-deny UDP OUTPUT..."
iptables -A OUTPUT -p udp -m owner --uid-owner 9999 -j ACCEPT
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
