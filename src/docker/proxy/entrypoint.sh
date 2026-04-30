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
# Issue #1060: Redirect CoreDNS stdout to dns.log when log dir is mounted.
LOG_DIR="/var/log/proxy"
if [ -d "$LOG_DIR" ]; then
    setpriv --reuid=9998 --regid=9998 --clear-groups -- \
        coredns -conf "$COREFILE" -dns.port 53 >> "$LOG_DIR/dns.log" 2>&1 &
else
    setpriv --reuid=9998 --regid=9998 --clear-groups -- \
        coredns -conf "$COREFILE" -dns.port 53 &
fi
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
#    This also covers the SOCKS5 listener on 127.0.0.1:1080 — agent processes
#    connect there via loopback, so the -o lo rule already permits that traffic.
#    The rule below is a defensive belt-and-suspenders ACCEPT for 1080 in case
#    a future rule reorder places a DROP before the lo rule.
iptables -A OUTPUT -o lo -j ACCEPT
iptables -A OUTPUT -p tcp -d 127.0.0.1 --dport 1080 \
    -m conntrack --ctstate NEW -j ACCEPT

# 2. Allow DNAT'd agent HTTP/HTTPS traffic to reach mitmproxy at $CONTAINER_IP:8080.
#    After nat OUTPUT DNAT, ip_route_me_harder() routes these via eth0 (not lo)
#    because $CONTAINER_IP is an eth0 address. The -o lo rule above does not
#    match; this rule fills the gap so they reach mitmdump.
iptables -A OUTPUT -p tcp -d "$CONTAINER_IP" --dport 8080 \
    -m conntrack --ctstate NEW -j ACCEPT

# 3. Allow established/related traffic (TCP handshake completion, return traffic).
iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT

# 4. Allow mitmdump (uid 9999) to initiate new upstream connections.
iptables -A OUTPUT -p tcp -m owner --uid-owner 9999 -m conntrack --ctstate NEW -j ACCEPT

# 5. Drop all other new outbound TCP (port 22, 8443, arbitrary ports, etc.).
# Issue #1060: NFLOG before DROP — NFLOG is per-network-namespace (unlike LOG which
# goes to the host kernel ring buffer) so ulogd2 inside the container can read it.
if [ -d "$LOG_DIR" ]; then
    iptables -A OUTPUT -p tcp -m conntrack --ctstate NEW \
        -j NFLOG --nflog-group 1 --nflog-prefix "PROXY_DROP_TCP "
fi
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
# Route mitmdump DNS queries to CoreDNS via loopback
iptables -t nat -A OUTPUT -p udp --dport 53 \
    -m owner --uid-owner 9999 \
    -j REDIRECT --to-ports 53
# Issue #1060: NFLOG before DROP for per-connection dropped UDP logging.
if [ -d "$LOG_DIR" ]; then
    iptables -A OUTPUT -p udp \
        -j NFLOG --nflog-group 1 --nflog-prefix "PROXY_DROP_UDP "
fi
iptables -A OUTPUT -p udp -j DROP

# --- Start mitmdump with multiple modes as uid 9999 ---
# setpriv drops to uid/gid 9999 but retains CAP_NET_ADMIN as an ambient
# capability so mitmproxy can use privileged socket options if needed.
#
# Transparent mode: agent containers sharing this network namespace have their
# TCP 80/443 transparently intercepted via the DNAT rules above.
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
echo "Starting mitmdump (transparent + SOCKS5) on :8080 / 127.0.0.1:1080..."
echo "Addon: /etc/proxy/addon.py"
# Issue #1060: Add -w flag to write mitmproxy flow file when log dir is mounted.
MITM_EXTRA_ARGS=""
if [ -d "$LOG_DIR" ]; then
    MITM_EXTRA_ARGS="-w $LOG_DIR/mitm.flows"
fi
# Issue #1052: --mode socks5@127.0.0.1:1080 adds a SOCKS5 listener alongside
# the existing transparent HTTP/HTTPS listener. SSH uses SOCKS5; HTTP/HTTPS
# continues through transparent DNAT. The addon.py tcp_start hook enforces the
# allowlist for SOCKS5 connections (IP literals rejected; hostnames checked).
PYTHONUNBUFFERED=1 setpriv --reuid=9999 --regid=9999 --clear-groups \
    --inh-caps +net_admin --ambient-caps +net_admin -- \
    mitmdump --mode transparent --mode "socks5@127.0.0.1:1080" --showhost -v \
    --listen-port 8080 \
    --set confdir="$CERTS_DIR" \
    --ssl-insecure \
    -s /etc/proxy/addon.py \
    --set block_global=false \
    --set stream_large_bodies=1m \
    --set connection_strategy=lazy \
    $MITM_EXTRA_ARGS &
MITM_PID=$!

# Issue #1060: Start ulogd2 to capture per-connection NFLOG drop events.
# NFLOG is per-network-namespace (unlike iptables -j LOG which writes to the
# host kernel ring buffer). ulogd2 reads from NFLOG group 1 and writes one
# JSON object per dropped packet to dropped.log via the JSON output plugin.
if [ -d "$LOG_DIR" ]; then
    cat > /tmp/ulogd.conf << EOF
[global]
logfile="/proc/1/fd/2"
loglevel=3
stack=nflog1:NFLOG,base1:BASE,ifi1:IFINDEX,ip2str1:IP2STR,print1:PRINTPKT,logemu1:LOGEMU

[nflog1]
group=1

[logemu1]
file="$LOG_DIR/dropped.log"
sync=1
EOF
    # Debian package installs binary as /usr/sbin/ulogd (not ulogd2)
    ulogd -d -c /tmp/ulogd.conf
fi

# Signal to claude-docker that iptables DNAT rules + mitmdump are fully up.
# Polling only the CA cert file is insufficient — it appears during cert-gen,
# before iptables setup and mitmdump startup. The .ready marker is written here,
# after all three are complete, so the host-side poll starts the agent at the
# right time.
# Verify mitmdump survived startup before signalling ready.
# A short sleep gives it time to parse the addon, read credentials, and bind :8080.
# If it crashes during that window (bad addon, unreadable files, port conflict),
# kill -0 will fail and we exit with a diagnostic message.
sleep 2
if ! kill -0 "$MITM_PID" 2>/dev/null; then
    echo "ERROR: mitmdump (PID $MITM_PID) exited during startup." >&2
    wait "$MITM_PID" 2>/dev/null
    exit 1
fi

# --- Optional: start session-scoped ssh-agent when a private key is mounted ---
# Issue #1052: When CLAUDE_DOCKER_SSH_KEY_DIR is mounted into the proxy at
# /run/ssh-private (read-only), the proxy runs ssh-agent and ssh-adds the key,
# then wipes the key file. The agent container gets only the socket — never the
# key bytes. The socket and SSH config live in the shared /run/ssh mount.
SSH_PRIVATE_KEY="/run/ssh-private/id"
SSH_SHARED_DIR="/run/ssh"
if [ -f "$SSH_PRIVATE_KEY" ]; then
    echo "SSH key found at $SSH_PRIVATE_KEY — starting session-scoped ssh-agent"
    mkdir -p "$SSH_SHARED_DIR"

    # Start ssh-agent as uid 1000 (matches claude user in agent container) so
    # the agent container's claude user can connect to the socket.
    SSH_AGENT_SOCK="$SSH_SHARED_DIR/agent.sock"
    eval "$(setpriv --reuid=1000 --regid=1000 --clear-groups -- \
        ssh-agent -a "$SSH_AGENT_SOCK" -s 2>/dev/null)"
    SSH_AGENT_PID=$!
    echo "ssh-agent started (pid=$SSH_AGENT_PID, sock=$SSH_AGENT_SOCK)"

    # Load the private key into the agent
    SSH_AUTH_SOCK="$SSH_AGENT_SOCK" setpriv --reuid=1000 --regid=1000 --clear-groups -- \
        ssh-add "$SSH_PRIVATE_KEY" 2>/dev/null

    # Verify at least one identity was loaded
    IDENTITY_COUNT=$(SSH_AUTH_SOCK="$SSH_AGENT_SOCK" setpriv --reuid=1000 --regid=1000 --clear-groups -- \
        ssh-add -l 2>/dev/null | wc -l || echo 0)
    if [ "$IDENTITY_COUNT" -lt 1 ]; then
        echo "ERROR: ssh-add succeeded but no identities in agent" >&2
        exit 1
    fi
    echo "ssh-agent loaded $IDENTITY_COUNT identity/identities"

    # Wipe private key from disk immediately after loading into agent memory.
    # The file lives on a host tmpdir; shred is best-effort (tmpfs has no disk sectors).
    if command -v shred >/dev/null 2>&1; then
        shred -u "$SSH_PRIVATE_KEY" 2>/dev/null || rm -f "$SSH_PRIVATE_KEY"
    else
        rm -f "$SSH_PRIVATE_KEY"
    fi
    echo "Private key file wiped from $SSH_PRIVATE_KEY"

    # Ensure the socket is accessible by the agent container's claude user (uid 1000).
    chmod 0666 "$SSH_AGENT_SOCK" 2>/dev/null || true

    # Create ssh-wrapper to route SSH via SOCKS5 and use a writable known_hosts
    SSH_WRAPPER="$SSH_SHARED_DIR/ssh-wrapper"
    cat > "$SSH_WRAPPER" <<'WRAPPER'
#!/bin/sh
exec ssh \
  -o "ProxyCommand=nc -X 5 -x 127.0.0.1:1080 %h %p" \
  -o "UserKnownHostsFile=/run/ssh/known_hosts" \
  -o "StrictHostKeyChecking=accept-new" \
  "$@"
WRAPPER
    chmod 0755 "$SSH_WRAPPER"
    chown 1000:1000 "$SSH_WRAPPER"

    # Create writable known_hosts owned by claude user (uid 1000)
    touch "$SSH_SHARED_DIR/known_hosts"
    chown 1000:1000 "$SSH_SHARED_DIR/known_hosts"
    chmod 0644 "$SSH_SHARED_DIR/known_hosts"
fi

touch "$CERTS_DIR/.ready"

# Forward signals to child processes so Docker stop works cleanly.
trap 'kill "$MITM_PID" "$COREDNS_PID" 2>/dev/null; wait' TERM INT

echo "Proxy ready (coredns PID=$COREDNS_PID, mitmdump PID=$MITM_PID)"
wait "$MITM_PID"
