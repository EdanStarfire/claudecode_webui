"""Shared networking helpers usable by both the Frontend API and Backend tiers."""

import socket


def allocate_free_port() -> int:
    """Bind a throwaway socket on 127.0.0.1:0 to obtain a free OS-assigned port.

    Avoids a fixed offset, which collides when multiple frontend/backend pairs
    run on one host (issue #1825).
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
