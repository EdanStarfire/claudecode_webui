FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    pipx \
    iptables \
    curl \
    jq \
    ca-certificates \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install mitmproxy via pipx (isolated, no system conflicts)
RUN pipx install mitmproxy && pipx ensurepath
ENV PATH="/root/.local/bin:${PATH}"

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
