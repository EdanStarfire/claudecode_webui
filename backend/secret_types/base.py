"""Abstract base class for secret-type handlers (issue #1134)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class SecretTypeHandler(ABC):
    """Stateless handler for one SecretType.

    At call time, `record` is a plain dict (from SecretRecord.to_dict() plus
    a `value` key holding the plaintext secret from the keyring).

    `flow` is a duck-typed mitmproxy HTTPFlow (real at runtime inside the proxy
    container; a mock in tests).
    """

    @abstractmethod
    def inject(self, flow: Any, record: dict, placeholder: str) -> bool:
        """Substitute placeholder→value on the outbound request flow.

        Returns True if the flow was modified.
        """

    @abstractmethod
    def scrub(self, flow: Any, record: dict, placeholder: str) -> tuple[bool, str | None]:
        """Replace raw value→placeholder in the inbound response flow.

        Returns (modified, captured_new_value_or_none).
        captured_new_value is set when a scrub matcher fires on a matching
        response URL and update_on_change is True, signalling that the keyring
        should be updated with the new value.
        """

    def should_refresh(self, record: dict) -> bool:
        """Return True if this record needs a token refresh before next injection.

        Default: never. Overridden by OAuth2Handler.
        """
        return False

    async def refresh(self, record: dict, get_secret_value: Any) -> dict:
        """Perform a token refresh and return updated record fields.

        `get_secret_value(name)` is a coroutine that fetches the current keyring
        value for a sibling secret record by name.

        Raises NotImplementedError for types that do not support refresh.
        """
        raise NotImplementedError(f"{type(self).__name__} does not support refresh")
