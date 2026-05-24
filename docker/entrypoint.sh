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

exec claude "$@"
