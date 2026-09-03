"""
Docker utilities for Claude Code WebUI (issue #496).

Provides Docker availability checking and wrapper script path resolution
for Docker session isolation.

Issue #1052: prepare_session_ssh() — SSH key tmpfs delivery for proxy-mode sessions.
"""

import asyncio
import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

# Default Docker image name used by the bundled wrapper
DEFAULT_DOCKER_IMAGE = "claude-code:local"


def get_wrapper_script_path() -> Path:
    """Return the absolute path to the bundled claude-docker wrapper script."""
    return Path(__file__).parent / "docker" / "claude-docker"


async def check_docker_available() -> dict:
    """
    Check if Docker is installed and accessible.

    Returns:
        dict with keys:
            - available (bool): True if Docker daemon is reachable
            - version (str | None): Docker version string, or None if unavailable
            - image_exists (bool): True if the default claude-code:local image exists
            - wrapper_exists (bool): True if the bundled wrapper script exists
    """
    result = {
        "available": False,
        "version": None,
        "image_exists": False,
        "wrapper_exists": get_wrapper_script_path().exists(),
    }

    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "version", "--format", "{{.Server.Version}}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        if proc.returncode == 0:
            result["available"] = True
            result["version"] = stdout.decode().strip() or None
    except FileNotFoundError:
        logger.debug("Docker CLI not found on PATH")
        return result
    except TimeoutError:
        logger.warning("Docker version check timed out")
        return result
    except Exception as e:
        logger.debug(f"Docker availability check failed: {e}")
        return result

    # Check if the default image exists
    if result["available"]:
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "image", "inspect", DEFAULT_DOCKER_IMAGE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=10)
            result["image_exists"] = proc.returncode == 0
        except Exception:
            pass

    return result


def resolve_docker_cli_path(
    docker_image: str | None = None,
    docker_extra_mounts: list[str] | None = None,
    workspace: str | None = None,
    session_data_dir: str | None = None,
    docker_home_directory: str | None = None,
    # Issue #1049: Proxy mode
    proxy_image: str | None = None,
    # Issue #1134: delivery env vars passed inline (replaces file-based approach)
    delivery_envs: dict[str, str] | None = None,
    # Issue #1396: non-secret direct env passthrough (no proxy substitution)
    extra_env: dict[str, str] | None = None,
    # Issue #1053: Dynamic allowlist override
    proxy_allowlist_file: str | None = None,
    # Proxy callback URL — passed as WEBUI_BASE_URL env var into the sidecar container so
    # it calls back on the correct port (avoids the hardcoded :8000 default in addon.py).
    proxy_webui_url: str | None = None,
    # Issue #1179: Proxy-sidecar-only mounts (session_token, session_id, etc.)
    docker_proxy_extra_mounts: list[str] | None = None,
    # Issue #1356: Session ID for container label lookup
    session_id: str | None = None,
) -> tuple[str, dict[str, str]]:
    """
    Resolve the cli_path and environment variables for Docker mode.

    Args:
        docker_image: Custom Docker image name (None = default)
        docker_extra_mounts: Additional volume mount specs
        workspace: Working directory to mount in the container
        session_data_dir: Host-side directory for persistent Claude session data.
                          Mounted as /home/claude/.claude/ inside the container so that
                          session transcripts survive container restarts (enabling --resume).
        docker_home_directory: Home directory inside the container (for custom images).
                               Sets CLAUDE_DOCKER_HOME env var. Default: /home/claude.
        proxy_image: Proxy sidecar image name (enables proxy mode when set).
                     claude-docker launches a dedicated sidecar per session and wires
                     the agent into its network namespace automatically.
        delivery_envs: Dict of env_var_name → placeholder string. Passed inline to
                       claude-docker via CLAUDE_DOCKER_DELIVERY_ENVS (JSON). The wrapper
                       forwards these as -e flags to the agent container. Vault secrets
                       win over extra_env when the same key appears in both (delivery_envs
                       is placed after EXTRA_ENV_FLAGS in the docker run argv).
        extra_env: Dict of env_var_name → literal value. Non-secret direct passthrough;
                   no proxy substitution. Serialised into CLAUDE_DOCKER_EXTRA_ENV (JSON).
                   Placed before DELIVERY_ENV_FLAGS in docker run so vault secrets win
                   on key conflicts. Do not put secrets here — use assigned_secrets.
        docker_proxy_extra_mounts: Additional volume mount specs applied exclusively to the
                                   proxy sidecar container (never the agent container).
        session_id: Session UUID; when set, emits CLAUDE_DOCKER_SESSION_ID so that
                    claude-docker can label the container for later lookup.

    Returns:
        Tuple of (cli_path_string, env_vars_dict)
    """
    import json as _json

    wrapper_path = str(get_wrapper_script_path())
    env_vars = {}

    if docker_image:
        env_vars["CLAUDE_DOCKER_IMAGE"] = docker_image

    if docker_extra_mounts:
        env_vars["CLAUDE_DOCKER_EXTRA_MOUNTS"] = ",".join(docker_extra_mounts)

    if workspace:
        env_vars["CLAUDE_DOCKER_WORKSPACE"] = workspace

    if session_data_dir:
        env_vars["CLAUDE_DOCKER_DATA_DIR"] = session_data_dir

    if docker_home_directory:
        env_vars["CLAUDE_DOCKER_HOME"] = docker_home_directory

    if proxy_image:
        env_vars["CLAUDE_DOCKER_PROXY_IMAGE"] = proxy_image

    if proxy_webui_url:
        env_vars["CLAUDE_DOCKER_PROXY_WEBUI_URL"] = proxy_webui_url

    if extra_env:
        env_vars["CLAUDE_DOCKER_EXTRA_ENV"] = _json.dumps(extra_env)

    if delivery_envs:
        env_vars["CLAUDE_DOCKER_DELIVERY_ENVS"] = _json.dumps(delivery_envs)

    if proxy_allowlist_file:
        env_vars["CLAUDE_DOCKER_PROXY_ALLOWLIST_FILE"] = proxy_allowlist_file

    if docker_proxy_extra_mounts:
        env_vars["CLAUDE_DOCKER_PROXY_EXTRA_MOUNTS"] = ",".join(docker_proxy_extra_mounts)

    if session_id:
        env_vars["CLAUDE_DOCKER_SESSION_ID"] = session_id

    return wrapper_path, env_vars


async def find_session_container(session_id: str, timeout: float = 5.0) -> str | None:
    """Return the running container ID for a session, or None if not running.

    Looks up by the cc-webui-session-id label set by claude-docker at session start.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "ps", "-q",
            "--filter", f"label=cc-webui-session-id={session_id}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode != 0:
            return None
        ids = stdout.decode().strip().splitlines()
        return ids[0] if ids else None
    except (TimeoutError, FileNotFoundError, OSError):
        return None


async def find_session_container_any_state(session_id: str, timeout: float = 5.0) -> str | None:
    """Return container ID for a session including stopped/exited containers.

    Unlike find_session_container (which uses docker ps), this uses docker ps -a
    so it can detect containers that have exited since session start.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "ps", "-a", "-q",
            "--filter", f"label=cc-webui-session-id={session_id}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode != 0:
            return None
        ids = stdout.decode().strip().splitlines()
        return ids[0] if ids else None
    except (TimeoutError, FileNotFoundError, OSError):
        return None


async def run_command_in_container(
    container_id: str,
    command_argv: list[str],
    timeout_seconds: float,
    workdir: str | None = None,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str, bool]:
    """Run an argv command inside a running container.

    Returns (exit_code, stdout, stderr, timed_out).
    On timeout, exit_code=-1 and timed_out=True.

    Args:
        env: Optional dict of environment variables to pass via --env KEY=value
             flags. Values are passed directly to docker exec (no shell parsing).
    """
    import contextlib

    args = ["docker", "exec"]
    if workdir:
        args.extend(["--workdir", workdir])
    if env:
        for k, v in env.items():
            args.extend(["--env", f"{k}={v}"])
    args.append(container_id)
    args.extend(command_argv)

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
        return (
            proc.returncode if proc.returncode is not None else -1,
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
            False,
        )
    except TimeoutError:
        proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()
        return (-1, "", "Script exceeded timeout", True)


async def run_command_on_host(
    command_argv: list[str],
    timeout_seconds: float,
    workdir: str | None = None,
) -> tuple[int, str, str, bool]:
    """Run an argv command on the host.

    Returns (exit_code, stdout, stderr, timed_out).
    On timeout, exit_code=-1 and timed_out=True.
    """
    import contextlib

    proc = await asyncio.create_subprocess_exec(
        *command_argv,
        cwd=workdir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
        return (
            proc.returncode if proc.returncode is not None else -1,
            stdout.decode(errors="replace"),
            stderr.decode(errors="replace"),
            False,
        )
    except TimeoutError:
        proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()
        return (-1, "", "Script exceeded timeout", True)


# SSH config mounted into the shared dir (agent container at /run/ssh).
# No IdentityFile — the ssh-agent socket inside the proxy sidecar provides
# the identity; the agent container never holds the raw private key bytes.
# IdentitiesOnly is intentionally absent so SSH_AUTH_SOCK (agent socket) is consulted.
_SSH_CONFIG_TEMPLATE = """\
Host *
    StrictHostKeyChecking accept-new
    UserKnownHostsFile /run/ssh/known_hosts
    ProxyCommand nc -X 5 -x 127.0.0.1:1080 %h %p
"""


def prepare_session_ssh(
    resolved_secrets: list[dict],
    key_dir: Path,
    shared_dir: Path,
) -> bool:
    """Prepare SSH key injection for a proxy-mode Docker session.

    Implements two-dir isolation so the private key bytes never enter the agent
    container:

      key_dir   — contains the raw private key file (``id``, mode 0o600).
                  Mounted into the PROXY SIDECAR ONLY at /run/ssh-private:ro.
                  The proxy entrypoint ssh-adds the key, then wipes this file.

      shared_dir — contains config, known_hosts, and agent socket.
                  Mounted into BOTH containers at /run/ssh (read-write on proxy
                  so ssh-agent can create the socket; read-only on agent).

    Returns True if an SSH key was prepared, False if no ssh_key secrets are
    assigned.  Raises ValueError if more than one ssh_key secret is assigned.
    """

    ssh_secrets = [s for s in resolved_secrets if s.get("type") == "ssh_key"]
    if not ssh_secrets:
        return False
    if len(ssh_secrets) > 1:
        names = [s.get("name", "?") for s in ssh_secrets]
        raise ValueError(
            f"At most one ssh_key secret may be assigned per session; got: {names}"
        )

    secret = ssh_secrets[0]
    key_value = secret.get("value", "")
    if not key_value:
        logger.warning(f"ssh_key secret '{secret.get('name')}' has empty value — skipping SSH setup")
        return False

    # key_dir: private key only — proxy-sidecar mount at /run/ssh-private:ro
    key_dir.mkdir(parents=True, exist_ok=True)
    key_path = key_dir / "id"
    content = key_value if key_value.endswith("\n") else key_value + "\n"
    key_path.write_text(content)
    os.chmod(key_path, 0o600)

    # shared_dir: socket + config + known_hosts — both containers at /run/ssh
    shared_dir.mkdir(parents=True, exist_ok=True)

    config_path = shared_dir / "config"
    config_path.write_text(_SSH_CONFIG_TEMPLATE)
    os.chmod(config_path, 0o644)

    known_hosts = shared_dir / "known_hosts"
    known_hosts.touch()
    os.chmod(known_hosts, 0o644)

    logger.info(
        f"SSH key prepared for secret '{secret.get('name')}' "
        f"(type={secret.get('key_type', 'unknown')}): "
        f"key_dir={key_dir}, shared_dir={shared_dir}"
    )
    return True


async def check_proxy_image_available(image_name: str) -> bool:
    """Check if a proxy Docker image exists locally. Returns True if found."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "image", "inspect", image_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=10)
        return proc.returncode == 0
    except Exception:
        return False


async def translate_docker_tmp_path(
    file_path: str,
    session_id: str,
    session_coordinator,
) -> str:
    """Translate /tmp paths to session-scoped tmp dir for Docker sessions."""
    if not file_path.startswith("/tmp/"):
        return file_path
    try:
        session_info = await session_coordinator.session_manager.get_session_info(session_id)
        if session_info and session_info.config.get("docker_enabled", False):
            session_dir = session_coordinator.data_dir / "sessions" / session_id
            relative = file_path[len("/tmp/"):]
            translated = str(session_dir / "tmp" / relative)
            logger.debug(f"Translated /tmp path for Docker session {session_id}: {translated}")
            return translated
    except Exception as e:
        logger.warning(f"Failed to check docker_enabled for path translation: {e}")
    return file_path


def get_session_tmp_dir(session_dir: Path) -> Path:
    """Get session's /tmp directory."""
    return session_dir / "tmp"


def cleanup_session_tmp(session_id: str, sessions_dir: Path) -> None:
    """Clean up session's temporary directory."""
    try:
        tmp_dir = sessions_dir / session_id / "tmp"
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)
            logger.info(f"Cleaned up /tmp for session {session_id}")
    except Exception:
        logger.exception(f"Failed to clean up /tmp for session {session_id}")
