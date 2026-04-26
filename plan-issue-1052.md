## Implementation Plan for Issue #1052

**Title:** SSH key isolation via ssh-agent socket in proxy sidecar + SOCKS5 proxy for agent containers

### User Stories

- [ ] **Story 1 — Operator assigns an SSH key to a session.** Operator stores SSH key material once (keyring-backed), assigns it to a session via the existing per-session secret-assignment UI, and sees the public-key fingerprint + OpenSSH-format public key for copy-paste into GitHub deploy keys.
- [ ] **Story 2 — Agent clones GitHub over SSH through the sidecar.** With a deploy key assigned and `github.com` on the allowlist, `git clone git@github.com:org/repo.git` succeeds. The connection is logged in the proxy access log with session token + destination.
- [ ] **Story 3 — Agent cannot reach SSH targets outside the allowlist.** SSH to non-allowlisted hostnames fails at CoreDNS (NXDOMAIN). SSH to non-allowlisted IP literals fails at the SOCKS5 layer (addon rejects IP-literal CONNECTs).
- [ ] **Story 4 — Key bytes never enter the agent container.** SSH private key material lives in the keyring at rest; at session start it is materialized only inside the proxy sidecar (separate from the agent container) and loaded into a session-scoped `ssh-agent` process. The agent container has no key file mount and no env var carrying key bytes — only the agent socket. The agent can sign with the key but cannot extract it.
- [ ] **Story 5 — Host SSH agent does not leak in proxy mode.** When `$PROXY_IMAGE` is set, claude-docker omits all `SSH_AUTH_SOCK` host-side forwarding flags. The only `SSH_AUTH_SOCK` inside the agent container points at the per-session ssh-agent socket inside the sidecar.

---

### Technical Approach

This issue ships **two coupled changes** to a single PR:

**A. SOCKS5 listener in the proxy container.** Extend the proxy container to run mitmdump in dual-mode: existing transparent listener on `:8080` (HTTP/HTTPS DNAT-intercept), plus a new SOCKS5 listener on `127.0.0.1:1080` for non-HTTP TCP. The agent uses SOCKS5 only for SSH; HTTP/HTTPS continues through transparent DNAT. Filter OUTPUT rules permit `127.0.0.1:1080` from agent uids. Allowlist enforcement at the SOCKS5 layer is a mitmdump addon hook that rejects IP-literal CONNECTs outright and rejects hostnames not in the loaded allowlist.

**B. Per-session ssh-agent socket in the proxy sidecar.** A new `ssh_key` secret type extends the typed-secrets framework (#1146). Keys are stored in the OS keyring; metadata persists the OpenSSH-format public key + SHA256 fingerprint (no secret material). At session launch, the backend resolves the assigned `ssh_key` secret and writes the PEM into a host tmpdir that is mounted **only into the proxy sidecar** (never into the agent). The proxy entrypoint starts `ssh-agent -a /run/ssh/agent.sock` and `ssh-add`s the key, then deletes the key file before signaling `.ready`. A separate **shared host tmpdir** is bind-mounted into both containers; the proxy ssh-agent creates the socket there and writes the SSH client config + per-session known_hosts. The agent container gets `SSH_AUTH_SOCK=/run/ssh/agent.sock` and `GIT_SSH_COMMAND="ssh -F /run/ssh/config"`. The SSH config has `ProxyCommand ncat --proxy 127.0.0.1:1080 --proxy-type socks5 %h %p`, `StrictHostKeyChecking accept-new`, `UserKnownHostsFile /run/ssh/known_hosts`, and **no `IdentityFile`** — the agent socket handles identity. Host SSH agent forwarding is disabled when `$PROXY_IMAGE` is set.

**Security property summary:**
- **Host:** key in OS keyring at rest. Materialized to a private-key tmpdir at session start; deleted by the proxy entrypoint immediately after `ssh-add` succeeds, then reaped by the EXIT trap.
- **Proxy sidecar:** runs ssh-agent holding the key in process memory. Key file is wiped after load. Only the sidecar can produce signatures.
- **Agent container:** has the agent socket and SSH config — never the key bytes. The SSH agent protocol does not expose private key material; it only signs challenges. Even root inside the agent cannot exfiltrate the key.

The agent base image (`src/docker/Dockerfile`) gains `openssh-client` and `ncat`. The proxy image (`src/docker/proxy/Dockerfile`) gains `openssh-client` (provides `ssh-agent`/`ssh-add`).

---

### Files to Modify

#### Proxy container (Component A + ssh-agent host)

- **`src/docker/proxy/Dockerfile`** — add `openssh-client` to apt-get install. Add `EXPOSE 1080`. mitmdump supports SOCKS5 natively; no other packages required.
- **`src/docker/proxy/entrypoint.sh`** — multiple additions:
  1. **mitmdump dual-mode** — change invocation to `--mode transparent --mode socks5@1080` so both listeners run.
  2. **iptables OUTPUT ACCEPT for 1080** — defensive allow rule for `-p tcp -d 127.0.0.1 --dport 1080 -m conntrack --ctstate NEW`, placed before the catchall TCP DROP. (Loopback traffic is already covered by `-o lo ACCEPT`, but the explicit rule documents intent.)
  3. **ssh-agent startup** — after mitmdump is up, if `/run/ssh-private/id` exists (the private-key bind-mount), start `ssh-agent -a /run/ssh/agent.sock` as a dedicated session uid (e.g., uid 1000 to match the agent container's `claude` user). Run `ssh-add /run/ssh-private/id`. **Verify** the load succeeded (`ssh-add -l` lists at least one identity) — exit nonzero if not. **Then** wipe the key file (`shred -u /run/ssh-private/id` if shred is available, otherwise `rm -f` — the file lives on a tmpfs-style host tmpdir so disk persistence is already minimized). Chmod the agent socket so the agent container's user can connect.
  4. **`.ready` marker** — only write the marker after ssh-agent is up AND the key file is wiped. Existing logic still applies for the no-SSH case (empty key dir → skip ssh-agent, write `.ready` as today).
- **`src/docker/proxy/addon.py`** — add a SOCKS5 connection-validation hook. mitmproxy exposes `tcp_start`; in SOCKS5 mode the destination host (CONNECT target) is on `flow.server_conn.address`. Implement:
  - If destination parses as an IP literal (`ipaddress.ip_address()` succeeds), `flow.kill()` and log.
  - Otherwise, check against the in-memory allowlist set (loaded once at startup from `/etc/proxy/allowlist.json`). If not matched, `flow.kill()` and log.
  - Allowed connections proceed; mitmproxy tunnels them as opaque TCP. Decisions logged to `/var/log/proxy/socks5.log` when `LOG_DIR` is mounted.

#### SSH key secret type (Component B — backend)

- **`src/secret_types/__init__.py`** — replace `"ssh": GenericHandler()` with `"ssh_key": SshKeyHandler()` and import. Audit for any lingering `"ssh"` literal references (none expected; the previous mapping was a placeholder).
- **`src/secret_types/ssh_key.py`** — **NEW**. Subclass `SecretTypeHandler`:
  - `inject()` and `scrub()` are no-ops returning `(False, None)` — SSH traffic is opaque tunneling through SOCKS5.
  - New method `validate(secret_value: str) -> SshKeyValidation` — parse the PEM with `cryptography.hazmat.primitives.serialization.load_ssh_private_key()`. Reject passphrase-protected keys (catch `TypeError: Password was given but private key is not encrypted` and the equivalent `Bad password / not encrypted` exceptions; raise a clear `SshKeyValidationError`). Returns `(public_key_openssh: str, fingerprint_sha256: str, key_type: str)`.
  - New method `materialize(secret_value: str, target_dir: Path) -> Path` — writes the private key to `target_dir/id` with mode 0o600 and returns the path. Used at session-launch by the orchestrator; the path lives on the **private-key tmpdir** that mounts only into the proxy sidecar.
- **`src/credential_vault.py`** (or wherever metadata is stored) — when a secret of type `ssh_key` is created or updated, call `SshKeyHandler.validate()`, then persist `public_key_openssh` and `fingerprint_sha256` in metadata JSON. Public key is non-sensitive; metadata file at `data/credentials/{name}.json` (mode 0o600 per existing convention).
- **`src/docker_utils.py`** — extend session-launch orchestration with a new `prepare_session_ssh()` helper that:
  1. Filters `assigned_secrets` to `ssh_key` type. Raises if more than one is assigned (one-per-session enforcement).
  2. Allocates **two host tmpdirs**:
     - `key_dir` — private-key location, mounted only into the proxy sidecar.
     - `shared_dir` — socket + SSH config + known_hosts, mounted into both containers.
  3. Calls `SshKeyHandler.materialize(value, key_dir)` to write the PEM (mode 0o600).
  4. Renders the SSH client config into `shared_dir/config`:
     ```
     Host *
         IdentitiesOnly yes
         StrictHostKeyChecking accept-new
         UserKnownHostsFile /run/ssh/known_hosts
         ProxyCommand ncat --proxy 127.0.0.1:1080 --proxy-type socks5 %h %p
     ```
     (No `IdentityFile` line — agent socket provides identity.)
  5. Touches an empty `shared_dir/known_hosts` (writable for `accept-new` TOFU entries; mode 0o600 owned by the agent uid).
  6. Exports two new env vars consumed by claude-docker:
     - `CLAUDE_DOCKER_SSH_KEY_DIR=<key_dir>` → mounted into proxy only at `/run/ssh-private:ro`.
     - `CLAUDE_DOCKER_SSH_SHARED_DIR=<shared_dir>` → mounted into both proxy and agent at `/run/ssh`.
  7. Both tmpdirs adopted by claude-docker's existing EXIT trap for cleanup.

#### claude-docker script (Component A + B integration)

- **`src/docker/claude-docker`**:
  - **Lines 147-150** — wrap the `SSH_AGENT_FLAGS` block in `if [ -z "$PROXY_IMAGE" ]; then ... fi`. In proxy mode, the host agent socket is never forwarded.
  - **Proxy launch (around line 214)** — when `CLAUDE_DOCKER_SSH_KEY_DIR` is set, add `-v "$CLAUDE_DOCKER_SSH_KEY_DIR:/run/ssh-private:ro"` to the proxy `docker run`. When `CLAUDE_DOCKER_SSH_SHARED_DIR` is set, add `-v "$CLAUDE_DOCKER_SSH_SHARED_DIR:/run/ssh"` (read-write so ssh-agent can create the socket and known_hosts can be appended).
  - **Agent launch (around line 281)** — when `CLAUDE_DOCKER_SSH_SHARED_DIR` is set, add `-v "$CLAUDE_DOCKER_SSH_SHARED_DIR:/run/ssh"` (matches proxy mount; **NOT** a key file mount), plus `-e SSH_AUTH_SOCK=/run/ssh/agent.sock` and `-e GIT_SSH_COMMAND="ssh -F /run/ssh/config"`. The agent gets no mount for `/run/ssh-private/`.
  - **EXIT trap (line 205)** — extend to `rm -rf "$CLAUDE_DOCKER_SSH_KEY_DIR" "$CLAUDE_DOCKER_SSH_SHARED_DIR" 2>/dev/null || true` so both tmpdirs are reaped.

#### Agent base image

- **`src/docker/Dockerfile`** — add `openssh-client` and `ncat` to the apt-get install line. Verify `ncat` is available as a standalone package on Debian bookworm-slim; fall back to `nmap` if it isn't (note in PR if so).

#### Frontend (Component B — UI)

- **`frontend/src/components/configuration/SecretsTab.vue`** — add `ssh_key` to the type selector with:
  - Multiline textarea for pasting the private key (PEM).
  - On submit: POST to existing credential-create endpoint; backend validates, derives public key, persists.
  - Display section showing `public_key_openssh`, `fingerprint_sha256`, `key_type` once stored.
  - "Copy public key" button (clipboard API).
  - "Copy fingerprint" button.
  - Surface backend errors inline (passphrase-protected, malformed PEM).
- **`frontend/src/stores/proxy.js`** — extend the credential-metadata response shape so `public_key_openssh` and `fingerprint_sha256` are surfaced when `type == "ssh_key"`. No new endpoint.

#### Configuration / env-var plumbing

- **`src/config_manager.py` / `src/session_config.py`** — no schema changes. SSH key assignment reuses `assigned_secrets: list[str] | None`. The orchestrator filters by type at launch.

---

### Implementation Steps

1. **Add `openssh-client` and `ncat` to `src/docker/Dockerfile`.** Build the agent image; confirm both binaries are on PATH.

2. **Add `openssh-client` to `src/docker/proxy/Dockerfile`** and `EXPOSE 1080`. Confirm `ssh-agent` and `ssh-add` are present in the proxy image.

3. **Add SOCKS5 dual-mode + filter ACCEPT to `src/docker/proxy/entrypoint.sh`.** Build & smoke-test: confirm both `:8080` and `:1080` listen, transparent mode still works, the new ACCEPT rule doesn't shadow existing rules.

4. **Add SOCKS5 allowlist enforcement in `src/docker/proxy/addon.py`** (`tcp_start` hook with IP-literal rejection + hostname allowlist check + access logging).

5. **Extend `src/docker/proxy/entrypoint.sh` for ssh-agent.** When `/run/ssh-private/id` exists: start `ssh-agent -a /run/ssh/agent.sock` (under the matching uid), `ssh-add` the key, verify with `ssh-add -l`, wipe the key file, chmod the socket appropriately. Only then `touch .ready`. Skip cleanly when no key is mounted.

6. **Implement `src/secret_types/ssh_key.py`** with `SshKeyHandler(SecretTypeHandler)`:
   - `inject()`/`scrub()` no-ops.
   - `validate()` parses PEM, rejects passphrase-protected, returns derived public-key form + fingerprint.
   - `materialize()` writes private key to a target dir with mode 0o600.
   - Register in `src/secret_types/__init__.py`.

7. **Extend credential vault metadata** to store `public_key_openssh` and `fingerprint_sha256` for `ssh_key` secrets. Update create/update flows to call `validate()` and persist.

8. **Extend `src/docker_utils.py`** with `prepare_session_ssh()`: allocate the two tmpdirs, materialize the key into the private dir, write SSH config + empty known_hosts into the shared dir, export the two `CLAUDE_DOCKER_SSH_*` env vars.

9. **Update `src/docker/claude-docker`**:
   - Gate `SSH_AGENT_FLAGS` on `[ -z "$PROXY_IMAGE" ]`.
   - Mount `CLAUDE_DOCKER_SSH_KEY_DIR` into proxy only.
   - Mount `CLAUDE_DOCKER_SSH_SHARED_DIR` into both containers; set `SSH_AUTH_SOCK` + `GIT_SSH_COMMAND` env on the agent.
   - Extend EXIT trap to reap both tmpdirs.

10. **Frontend SecretsTab.vue**: add `ssh_key` form path, public-key + fingerprint display, copy buttons, error rendering.

11. **Run quality checks**: `uv run ruff check --fix src/`, `npm run build` in `frontend/`.

12. **Manual end-to-end verification** against the test scenarios below.

---

### Testing Strategy

#### Unit tests

- **`src/tests/test_ssh_key_secret.py`** (NEW):
  - `validate()` accepts ed25519, RSA, ECDSA OpenSSH PEM keys.
  - `validate()` rejects passphrase-protected keys with the expected error.
  - `validate()` rejects malformed PEM.
  - `validate()` returns correct `fingerprint_sha256` (compare against `ssh-keygen -lf`).
  - `materialize()` writes file with mode 0o600.

- **`src/tests/test_docker_utils_ssh.py`** (NEW or extend `test_docker_utils.py`):
  - `prepare_session_ssh()` allocates the two tmpdirs and writes the expected files.
  - Raises when multiple ssh_key secrets are assigned to the same session.
  - SSH config contains the expected `ProxyCommand` + `UserKnownHostsFile` + **no** `IdentityFile`.
  - Env vars `CLAUDE_DOCKER_SSH_KEY_DIR` and `CLAUDE_DOCKER_SSH_SHARED_DIR` are populated.
  - Tmpdirs removable by simulated cleanup.

- **`src/tests/test_socks5_addon.py`** (NEW):
  - Allowlisted hostname → flow proceeds.
  - Non-allowlisted hostname → `flow.kill()` called.
  - IP-literal destination → `flow.kill()` called.

#### Integration tests

- **Proxy container smoke test**: build proxy image, run with a synthetic key in `/run/ssh-private/id`, confirm:
  - `ssh-agent` is running, `ssh-add -l` lists the key.
  - `/run/ssh-private/id` is gone after startup.
  - `/run/ssh/agent.sock` exists with permissions allowing the agent uid.
  - Both `:8080` and `:1080` are listening.
  - `.ready` is written.
- **claude-docker dry-run**: with `$PROXY_IMAGE`, `$CLAUDE_DOCKER_SSH_KEY_DIR`, and `$CLAUDE_DOCKER_SSH_SHARED_DIR` set, inspect generated `docker run` flags (e.g. via `bash -x` or a `--print-args` mode behind an env var). Confirm:
  - `SSH_AGENT_FLAGS` is empty (no host agent forwarding).
  - Proxy run line mounts `:/run/ssh-private:ro` AND `:/run/ssh`.
  - Agent run line mounts `:/run/ssh` only (NOT `/run/ssh-private`) AND sets `SSH_AUTH_SOCK` + `GIT_SSH_COMMAND`.

#### Manual verification (the four issue scenarios + key isolation)

1. **GitHub clone over SSH** — Create an `ssh_key` secret, paste a GitHub deploy key, assign to a session with `github.com` on the allowlist. Run `git clone git@github.com:owner/repo.git` inside the agent container. Confirm clone succeeds; proxy access log shows the connection with session token + `github.com:22`.
2. **SSH to non-allowlisted host** — From the same session run `ssh user@example.com` (assuming example.com not allowlisted). Confirm SOCKS5 addon rejects, agent sees a clear error, rejection logged.
3. **Key not on disk after stop** — Stop the container. Verify both tmpdirs are deleted from the host. Run `find / -name 'id' -path '*ssh*' 2>/dev/null` to confirm no residue.
4. **No host SSH agent access** — Host `ssh-agent` running with multiple keys. Launch a proxy-mode session. Inside the agent: `echo $SSH_AUTH_SOCK` shows `/run/ssh/agent.sock` (the sidecar one), `ssh-add -L` lists ONLY the session deploy key (NOT host keys).
5. **Key bytes inaccessible from agent** — Inside the agent container as root: `find / -name 'id_ed25519' -o -name 'id_rsa' 2>/dev/null` returns nothing. Inspect `/proc/*/maps` for the running ssh process to confirm no key bytes are mapped (key memory lives in the sidecar's ssh-agent only). Attempt `ssh-add -L` to confirm only public key fingerprints are exposed by the protocol.

#### Frontend smoke

- Configuration → Secrets, add an SSH key, observe public key + fingerprint render. Copy public key, paste into `ssh-keygen -lf -` to confirm fingerprint round-trip.
- Reject a passphrase-protected key, observe inline error.

---

### Risks & Considerations

- **Socket ownership across containers** — ssh-agent creates the socket with mode 0600 owned by its uid. The agent container's `claude` user (uid 1000 by `useradd -m`) must be able to `connect()` to it. Mitigation: run ssh-agent in the proxy under a setpriv-dropped uid 1000 (matching the agent container), OR create the socket then `chmod 0666` (less ideal) OR set the `--bind-address` and explicit umask. The first option is preferred and cleanly verifiable.
- **Key wipe before `.ready`** — must guarantee the private key file is removed before agent start. Mitigation: explicit `shred -u || rm -f` in the entrypoint between `ssh-add` and `touch .ready`. Verify in the integration smoke test.
- **`ncat` package availability** — Debian bookworm-slim should expose `ncat` standalone; fall back to `nmap` if not. Confirm during implementation.
- **mitmdump SOCKS5 hook signature** — `tcp_start` event in mitmproxy 12.x must be verified. Mitigation: pin mitmproxy version in the proxy Dockerfile; document the addon API contract.
- **mitmproxy SOCKS5 implementation may not invoke addon hooks the same way as transparent mode** — write the addon defensively (fail-closed if destination metadata is missing).
- **Tmpdir ownership conflict** — proxy entrypoint chowns `PROXY_CERTS` to uid 9999. The new SSH tmpdirs must NOT be inside `PROXY_CERTS` (or be excluded from the chown). Mitigation: allocate the SSH tmpdirs as siblings to `PROXY_CERTS` and have the proxy entrypoint set ownership only on the specific paths it uses (not blanket on the SSH tmpdirs).
- **Standard `~/.ssh/config` lookup** — using `/run/ssh/config` plus `GIT_SSH_COMMAND="ssh -F /run/ssh/config"` covers git (Story 2). Bare `ssh` invocations need `-F /run/ssh/config` or a startup symlink to `~/.ssh/config`. Mitigation: document `ssh -F /run/ssh/config` as the supported invocation and rely on `GIT_SSH_COMMAND` for the common git path. Consider an `ENTRYPOINT` step or post-start helper that symlinks `~/.ssh/config` → `/run/ssh/config` for ergonomic bare-ssh usage; not required for Story 2.
- **`cryptography` package availability** — confirm it's in backend deps; almost certainly already present via the OAuth2 secret type. `uv add cryptography` if missing.
- **Backward compatibility** — the change to `SSH_AGENT_FLAGS` only fires when `$PROXY_IMAGE` is set; non-proxy mode unchanged. The handler-registry rename `"ssh"` → `"ssh_key"` is a key-name change for a placeholder mapping; verify no persisted secret records reference `"ssh"` literally (the old entry was an unused stub).
- **ssh-agent process lifecycle** — when the proxy container stops, ssh-agent dies, the socket file disappears. Any in-flight signing requests fail; clients get a connection error. This matches normal ssh-agent semantics. No additional cleanup logic required beyond the existing EXIT trap reaping host tmpdirs.

---

### Estimated Scope

- **Files modified**: ~9
  - `src/docker/Dockerfile`
  - `src/docker/proxy/Dockerfile`
  - `src/docker/proxy/entrypoint.sh`
  - `src/docker/proxy/addon.py`
  - `src/docker/claude-docker`
  - `src/secret_types/__init__.py`
  - `src/credential_vault.py` (metadata extension)
  - `src/docker_utils.py` (`prepare_session_ssh`)
  - `frontend/src/components/configuration/SecretsTab.vue`
  - `frontend/src/stores/proxy.js` (metadata shape)

- **Files created**: ~4
  - `src/secret_types/ssh_key.py`
  - `src/tests/test_ssh_key_secret.py`
  - `src/tests/test_docker_utils_ssh.py`
  - `src/tests/test_socks5_addon.py`

- **Components**:
  - Backend: 1 new secret type, 1 new orchestration helper, 1 vault metadata extension
  - Proxy container: 1 new mitmdump mode, 1 new addon hook, 1 new iptables rule, ssh-agent host integration
  - claude-docker: SSH agent forwarding gate + new tmpdir mounts (asymmetric: key into proxy only, shared into both)
  - Frontend: 1 secrets-tab type extension

---

Plan ready for approval. Awaiting `/approve_plan 1052`.
