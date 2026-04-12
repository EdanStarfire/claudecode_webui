FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    pipx \
    iptables \
    iproute2 \
    util-linux \
    curl \
    jq \
    ca-certificates \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install mitmproxy system-wide so uid 9999 can execute mitmdump
ENV PIPX_HOME=/opt/pipx
ENV PIPX_BIN_DIR=/usr/local/bin
RUN pipx install mitmproxy

# Dedicated non-root user for mitmdump; UID 9999 used in iptables owner match
# to exclude mitmdump's upstream connections from TPROXY re-interception loop.
RUN useradd -r -u 9999 -s /bin/false mitmproxy

# Install CoreDNS
ARG COREDNS_VERSION=1.12.1
RUN curl -fsSL "https://github.com/coredns/coredns/releases/download/v${COREDNS_VERSION}/coredns_${COREDNS_VERSION}_linux_amd64.tgz" \
    | tar -xz -C /usr/local/bin/ \
    && chmod +x /usr/local/bin/coredns

# Copy configuration
COPY allowlist.json /etc/proxy/allowlist.json
COPY Corefile /etc/coredns/Corefile
COPY addon.py /etc/proxy/addon.py
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# CA cert output directory (mounted as volume at runtime)
VOLUME /root/.mitmproxy

# mitmdump: transparent HTTP/HTTPS (single port handles both)
EXPOSE 8080
# CoreDNS
EXPOSE 53/udp 53/tcp

ENTRYPOINT ["/entrypoint.sh"]
