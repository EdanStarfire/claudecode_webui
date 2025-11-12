"""
Utility functions for Legion multi-agent system.
"""


def normalize_channel_name(name: str) -> str:
    """
    Normalize channel name by removing leading # if present.

    Supports natural minion references using #channel-name format
    (following common chat system conventions like Slack, Discord, IRC)
    while storing channels internally without the # prefix.

    Args:
        name: Channel name, potentially with # prefix

    Returns:
        Channel name without # prefix

    Examples:
        >>> normalize_channel_name("#backend")
        'backend'
        >>> normalize_channel_name("backend")
        'backend'
        >>> normalize_channel_name("##backend")
        '#backend'
        >>> normalize_channel_name("#")
        ''
    """
    if name.startswith('#'):
        return name[1:]
    return name
