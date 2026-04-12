"""
Docker utilities for Claude Code WebUI (issue #496).

Provides Docker availability checking and wrapper script path resolution
for Docker session isolation.
"""

import asyncio
import logging
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
    proxy_host: str | None = None,
    session_token: str | None = None,
    proxy_network: str | None = None,
    proxy_ca_cert: str | None = None,
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
        proxy_host: IP/hostname of proxy container (enables proxy mode when set).
        session_token: Session token for proxy auth (required when proxy mode active).
        proxy_network: Docker network name (default: agent-net).
        proxy_ca_cert: Path to proxy CA cert on host (default: auto-detect from proxy).

    Returns:
        Tuple of (cli_path_string, env_vars_dict)
    """
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

    if proxy_host:
        env_vars["CLAUDE_DOCKER_PROXY_HOST"] = proxy_host
        if session_token:
            env_vars["CLAUDE_DOCKER_SESSION_TOKEN"] = session_token
        if proxy_network:
            env_vars["CLAUDE_DOCKER_NETWORK"] = proxy_network
        if proxy_ca_cert:
            env_vars["CLAUDE_DOCKER_PROXY_CA_CERT"] = proxy_ca_cert

    return wrapper_path, env_vars


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
        if session_info and getattr(session_info, "docker_enabled", False):
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
