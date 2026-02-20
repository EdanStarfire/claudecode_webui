FROM debian:bookworm-slim

# Install only what's needed: curl for downloading, git for claude operations,
# ca-certificates for HTTPS, and basic tools claude might want to use
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    git \
    jq \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -s /bin/bash claude

USER claude

# Install Claude Code using the official native installer
# This downloads the correct platform binary, verifies checksum, and installs it
RUN curl -fsSL https://claude.ai/install.sh | bash

# Ensure the binary is on PATH
ENV PATH="/home/claude/.local/bin:${PATH}"

# Workspace path and working directory set at runtime via docker run -w
ENTRYPOINT ["claude"]
