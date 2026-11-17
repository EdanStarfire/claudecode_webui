"""Shared networking helpers usable by both the Frontend API and Backend tiers."""

import socket
import threading
from collections import deque

_RECENT_PORT_MEMORY = 64
_MAX_ATTEMPTS = 20

_recent_ports: deque[int] = deque(maxlen=_RECENT_PORT_MEMORY)
_recent_lock = threading.Lock()


def allocate_free_port() -> int:
    """Bind a throwaway socket on 127.0.0.1:0 to obtain a free OS-assigned port.

    Avoids a fixed offset, which collides when multiple frontend/backend pairs
    run on one host (issue #1825). The kernel may reissue a just-closed port to
    the next bind(0), so ports handed out recently by this process are skipped
    (issue #2358).
    """
    for _ in range(_MAX_ATTEMPTS):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        with _recent_lock:
            if port not in _recent_ports:
                _recent_ports.append(port)
                return port
    raise RuntimeError("could not allocate a free port distinct from recent allocations")
