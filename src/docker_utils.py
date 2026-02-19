"""
Docker utilities for Claude Code WebUI (issue #496).

Provides Docker availability checking and wrapper script path resolution
for Docker session isolation.
"""

import asyncio
import logging
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
) -> tuple[str, dict[str, str]]:
    """
    Resolve the cli_path and environment variables for Docker mode.

    Args:
        docker_image: Custom Docker image name (None = default)
        docker_extra_mounts: Additional volume mount specs
        workspace: Working directory to mount in the container

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

    return wrapper_path, env_vars
