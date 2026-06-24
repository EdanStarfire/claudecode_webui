FROM debian:bookworm-slim

# Install only what's needed: curl for downloading, git for claude operations,
# ca-certificates for HTTPS, and basic tools claude might want to use
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean && \
    apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    git \
    jq \
    bubblewrap \
    openssh-client \
    ncat \
    netcat-openbsd

# ssh 7.3+ silently ignores an Include of a non-existent path — safe when no SSH secrets are configured.
RUN echo "Include /run/ssh/config" >> /etc/ssh/ssh_config

# Create non-root user
RUN useradd -m -s /bin/bash claude

USER claude

# Install Claude Code using the official native installer
# This downloads the correct platform binary, verifies checksum, and installs it
# Bump in lockstep with claude-agent-sdk in pyproject.toml/uv.lock.
ARG CLAUDE_CODE_VERSION=2.1.186
RUN curl -fsSL https://claude.ai/install.sh | bash -s ${CLAUDE_CODE_VERSION}

# Ensure the binary is on PATH
ENV PATH="/home/claude/.local/bin:${PATH}"

# Workspace path and working directory set at runtime via docker run -w
ENTRYPOINT ["claude"]
