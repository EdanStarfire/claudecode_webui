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
# How it works (TPROXY for locally-generated traffic):
#   1. mangle OUTPUT marks agent TCP 80/443 with fwmark 1.
#   2. Policy routing (ip rule) sends fwmark-1 packets to table 100.
#   3. Table 100 routes everything to loopback, re-injecting packets into
#      the PREROUTING chain as if they arrived from outside.
#   4. mangle PREROUTING TPROXY assigns packets directly to mitmdump's
#      listening socket via IP_TRANSPARENT, WITHOUT changing the destination.
#      mitmdump sees the original destination (e.g. 140.82.114.6:443) as the
#      accepted socket's local address.
#   Loop prevention: mitmdump runs as uid 9999; the mangle OUTPUT MARK rule
#      has ! --uid-owner 9999, so mitmdump's upstream connections are never
#      marked and never re-intercepted.
#
# Why TPROXY instead of REDIRECT/DNAT?
#   Both REDIRECT and DNAT in the nat OUTPUT chain fail for locally-generated
#   traffic: REDIRECT rewrites dst to 127.0.0.1 (kernel drops non-loopback src
#   on lo); DNAT to the container's bridge IP also silently fails to deliver
#   packets to the local socket. TPROXY avoids both issues by operating in
#   mangle PREROUTING after policy-routing re-injects packets through lo.

echo "Configuring TPROXY transparent proxy..."

# Step 1: Mark agent TCP 80/443 in mangle OUTPUT.
# uid 9999 (mitmdump) is excluded to prevent re-interception loops.
iptables -t mangle -A OUTPUT -p tcp --dport 80 \
    -m owner ! --uid-owner 9999 -j MARK --set-mark 1
iptables -t mangle -A OUTPUT -p tcp --dport 443 \
    -m owner ! --uid-owner 9999 -j MARK --set-mark 1

# Step 2: Policy routing — fwmark 1 → table 100 → local delivery via lo.
# This re-injects marked packets into PREROUTING as if they arrived externally.
ip rule add fwmark 1 lookup 100
ip route add local 0.0.0.0/0 dev lo table 100

# Step 3: TPROXY in mangle PREROUTING intercepts re-injected packets.
# --on-port 8080 delivers to mitmdump's listening socket.
# --tproxy-mark re-applies fwmark so return traffic uses the same routing.
iptables -t mangle -A PREROUTING -p tcp --dport 80 \
    -j TPROXY --tproxy-mark 0x1/0x1 --on-port 8080
iptables -t mangle -A PREROUTING -p tcp --dport 443 \
    -j TPROXY --tproxy-mark 0x1/0x1 --on-port 8080

# --- Default-deny TCP OUTPUT ---
# Block all TCP from non-proxy processes on ports other than 80/443, closing
# the raw-IP gap (non-HTTP/HTTPS TCP was previously unrestricted).
#
# CRITICAL ordering note: filter OUTPUT runs BEFORE the fwmark policy-routing
# reroute. When mangle OUTPUT sets fwmark 1, the packet still has its original
# output interface (eth0). The reroute to lo only happens AFTER all OUTPUT
# hooks complete. Therefore, fwmark-1 packets must be explicitly accepted in
# filter OUTPUT — they will NOT match the -o lo rule.
#
# Rule order:
#   1. lo ACCEPT: inter-process loopback communication (CoreDNS, mitmdump
#      return traffic, etc.).
#   2. fwmark 1 ACCEPT: TPROXY-bound agent TCP 80/443. These packets were
#      marked by mangle OUTPUT and will be rerouted to lo after filter, then
#      TPROXY-ed in PREROUTING. Must pass filter first since the reroute
#      hasn't happened yet (packet still shows -o eth0 at this point).
#   3. ESTABLISHED,RELATED ACCEPT: allows TCP handshakes to complete and
#      return traffic for established connections.
#   4. uid 9999 NEW ACCEPT: mitmdump upstream connections to the real internet.
#      Works because mitmdump is a child process (not PID 1); xt_owner uid
#      matching is reliable for non-PID-1 processes.
#   5. tcp NEW DROP: everything else (agent TCP to port 22, 8443, etc.).
echo "Configuring default-deny TCP OUTPUT..."

# 1. Allow all loopback traffic (inter-process comms).
iptables -A OUTPUT -o lo -j ACCEPT

# 2. Allow TPROXY-marked packets (fwmark 1) to pass filter before reroute.
# These are agent TCP 80/443 that mangle OUTPUT marked; they'll be rerouted
# to lo and TPROXY-ed in PREROUTING after filter OUTPUT completes.
iptables -A OUTPUT -m mark --mark 1 -j ACCEPT

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

# --- Start mitmdump in transparent mode as uid 9999 ---
# setpriv drops to uid/gid 9999 but retains CAP_NET_ADMIN as an ambient
# capability. TPROXY requires IP_TRANSPARENT on the listening socket, which
# needs CAP_NET_ADMIN. The --inh-caps and --ambient-caps flags promote
# the capability so it's effective for the unprivileged mitmdump process.
#
# IMPORTANT: do NOT use exec here. The mangle OUTPUT MARK rule uses
# "! --uid-owner 9999" to exclude mitmproxy's own upstream connections
# from being re-intercepted. The Linux kernel's xt_owner module does not
# reliably match uid for PID 1; if mitmdump were exec'd into PID 1, its
# upstream connections would fail the uid match and be re-marked back to
# itself (loop). Running as a child process (non-PID-1) ensures uid 9999
# is matched correctly by the owner module.
#
# PID 1 (this shell) remains as the container's init process and forwards
# SIGTERM/SIGINT to the child processes for clean shutdown.
echo "Starting mitmdump (transparent mode + TPROXY) on :8080..."
echo "Addon: /etc/proxy/addon.py"
PYTHONUNBUFFERED=1 setpriv --reuid=9999 --regid=9999 --clear-groups \
    --inh-caps +net_admin --ambient-caps +net_admin -- \
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
