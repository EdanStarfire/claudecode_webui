FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    pipx \
    iptables \
    iproute2 \
    util-linux \
    libcap2-bin \
    procps \
    curl \
    jq \
    ca-certificates \
    ulogd2 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install mitmproxy system-wide so uid 9999 can execute mitmdump
ENV PIPX_HOME=/opt/pipx
ENV PIPX_BIN_DIR=/usr/local/bin
RUN pipx install mitmproxy

# Dedicated non-root user for mitmdump; UID 9999 used in iptables owner match
# to exclude mitmdump's upstream connections from TPROXY re-interception loop.
RUN useradd -r -u 9999 -s /bin/false mitmproxy

# Dedicated non-root user for CoreDNS; UID 9998 exempted in UDP default-deny
# so CoreDNS can make upstream UDP DNS queries (e.g. to 8.8.8.8).
# setcap grants cap_net_bind_service on the binary so it can bind :53 as
# a non-root user without needing ambient capabilities at runtime.
RUN useradd -r -u 9998 -s /bin/false coredns

# Install CoreDNS
ARG COREDNS_VERSION=1.12.1
RUN curl -fsSL "https://github.com/coredns/coredns/releases/download/v${COREDNS_VERSION}/coredns_${COREDNS_VERSION}_linux_amd64.tgz" \
    | tar -xz -C /usr/local/bin/ \
    && chmod +x /usr/local/bin/coredns \
    && setcap cap_net_bind_service=+ep /usr/local/bin/coredns

# Copy configuration
COPY allowlist.json /etc/proxy/allowlist.json
COPY Corefile /etc/coredns/Corefile
COPY addon.py /etc/proxy/addon.py
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# CA cert output directory — outside /root/ so uid 9999 can traverse to it.
# /root/ is mode 700; any path under it is unreachable by non-root users.
RUN mkdir -p /var/lib/mitmproxy && chown 9999:9999 /var/lib/mitmproxy
VOLUME /var/lib/mitmproxy

# mitmdump: transparent HTTP/HTTPS interception
EXPOSE 8080
# CoreDNS
EXPOSE 53/udp 53/tcp

ENTRYPOINT ["/entrypoint.sh"]
