#!/bin/bash
PROXY_CA="/run/proxy-ca/ca.crt"
NSS_DB="${HOME}/.pki/nssdb"

if [ -f "${PROXY_CA}" ]; then
    mkdir -p "${NSS_DB}"
    certutil -N -d "sql:${NSS_DB}" --empty-password 2>/dev/null || true
    certutil -A -d "sql:${NSS_DB}" -n "ProxyCA" -t "CT,," -i "${PROXY_CA}" 2>/dev/null && \
        echo "[entrypoint] Proxy CA registered in Chromium NSS store." || \
        echo "[entrypoint] Warning: failed to register proxy CA." >&2
fi

# Frontend npm install staleness check (issue #2167): node_modules can't be
# baked into the image (bind-mount shadowing, see Dockerfile.dev Layer 6c's
# comment block), so run the real install here against the live workspace,
# resolving mostly from the image's pre-warmed npm cache. No-op if nothing
# changed since the last install.
FRONTEND_DIR="frontend"
if [ -f "${FRONTEND_DIR}/package.json" ]; then
    STAMP="${FRONTEND_DIR}/node_modules/.install-stamp"
    CURRENT_HASH=$(sha256sum "${FRONTEND_DIR}/package.json" "${FRONTEND_DIR}/.npmrc" 2>/dev/null | sha256sum | cut -d' ' -f1)
    if [ ! -f "${STAMP}" ] || [ "$(cat "${STAMP}" 2>/dev/null)" != "${CURRENT_HASH}" ]; then
        echo "[entrypoint] Installing frontend dependencies..."
        (cd "${FRONTEND_DIR}" && npm install) && echo "${CURRENT_HASH}" > "${STAMP}"
    fi
fi

exec claude "$@"
