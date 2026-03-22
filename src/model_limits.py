"""Model context window size lookup (issue #905)."""

MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "claude-opus-4-6": 1_000_000,
    "claude-opus-4-5": 1_000_000,
    "claude-sonnet-4-6": 200_000,
    "claude-sonnet-4-5": 200_000,
    "claude-sonnet-3-5": 200_000,
    "claude-haiku-4-5": 200_000,
    "claude-haiku-3-5": 200_000,
}


def get_context_window(model: str) -> int:
    """Return context window size for model, defaulting to 200k."""
    if not model:
        return 200_000
    for key, size in MODEL_CONTEXT_WINDOWS.items():
        if key in model:
            return size
    return 200_000
