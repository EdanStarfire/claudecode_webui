"""Single source for the Slack app manifest and scope documentation (issue #2103, UI stage 2a).

The relay acts with the owner's own user token (no bot user, no bot scopes). ``METHOD_SCOPES``
maps every Slack Web API method ``SlackAdapter`` calls to the user scopes it needs; a unit test
asserts that the adapter's methods and this table never drift apart, and that every scope in
``USER_SCOPES_REQUIRED`` is justified by at least one method.

Nothing here is per-relay data: the manifest is a pure function of these constants.
"""

from __future__ import annotations

import json
from typing import Any

APP_NAME = "ccWebUI Relay"
APP_DESCRIPTION = (
    "Lets the owner's ccWebUI agent sessions read and post in Slack on the owner's behalf "
    "using the owner's own user token. Not a bot."
)
APP_BACKGROUND_COLOR = "#2c3e50"

_HISTORY = ("channels:history", "groups:history", "im:history", "mpim:history")
_READ = ("channels:read", "groups:read", "im:read", "mpim:read")
_WRITE = ("channels:write", "groups:write", "im:write", "mpim:write")

# Slack Web API method -> user scopes it needs (auth.test needs none).
METHOD_SCOPES: dict[str, tuple[str, ...]] = {
    "auth.test": (),
    "chat.postMessage": ("chat:write",),
    "conversations.history": _HISTORY,
    "conversations.replies": _HISTORY,
    "conversations.info": _READ,
    "users.conversations": _READ,
    "conversations.mark": _WRITE,
    "conversations.open": ("im:write", "mpim:write"),
    "files.getUploadURLExternal": ("files:write",),
    "files.completeUploadExternal": ("files:write",),
    "reactions.add": ("reactions:write",),
    "search.messages": ("search:read",),
    "users.info": ("users:read",),
}

# scope -> reason shown in the setup guide.
USER_SCOPES_REQUIRED: dict[str, str] = {
    "chat:write": "Post and reply as you (post, reply tools).",
    "channels:history": "Read public channel messages and threads (read_thread).",
    "groups:history": "Read private channel messages and threads (read_thread).",
    "im:history": "Read direct message history and threads (read_thread).",
    "mpim:history": "Read group direct message history and threads (read_thread).",
    "channels:read": "Resolve public channel names, list your conversations (policies, picker).",
    "groups:read": "Resolve private channel names, list your conversations (policies, picker).",
    "im:read": "Resolve direct message names, list your conversations (policies, picker).",
    "mpim:read": "Resolve group direct message names, list your conversations (policies, picker).",
    "channels:write": "Mark public channels as read (mark_read).",
    "groups:write": "Mark private channels as read (mark_read).",
    "im:write": "Mark direct messages as read and open a DM by user ID (mark_read, post).",
    "mpim:write": "Mark group direct messages as read and open them (mark_read, post).",
    "files:write": "Upload files you choose to share (upload_file).",
    "reactions:write": "Add emoji reactions (react).",
    "search:read": "Search messages in conversations the policy allows (search).",
    "users:read": "Resolve user names for prompts and the audit log.",
}

# Alternatives, not part of the manifest: Slack is moving search to granular scopes.
USER_SCOPES_OPTIONAL: dict[str, str] = {
    "search:read.public": "Granular alternative to search:read: public channels.",
    "search:read.private": "Granular alternative to search:read: private channels.",
    "search:read.im": "Granular alternative to search:read: direct messages.",
    "search:read.mpim": "Granular alternative to search:read: group direct messages.",
    "search:read.files": "Granular alternative to search:read: files.",
}

# The inbound stage needs no scopes beyond the history scopes above.
USER_SCOPES_INBOUND: dict[str, str] = {}

USER_EVENTS_INBOUND: dict[str, str] = {
    "message.channels": "Messages in public channels you are in (monitoring).",
    "message.groups": "Messages in private channels you are in (monitoring).",
    "message.im": "Your direct messages, including your self-DM instructions.",
    "message.mpim": "Your group direct messages (monitoring).",
}

BOT_SCOPES: tuple[str, ...] = ()

SETUP_STEPS: list[dict[str, str]] = [
    {
        "title": "Create the Slack app from the manifest",
        "detail": "Open api.slack.com/apps, choose Create New App, From a manifest, pick your "
        "workspace, paste the manifest JSON below and create the app.",
    },
    {
        "title": "Install the app to your workspace",
        "detail": "On the app's Install App page choose Install to Workspace and allow the "
        "requested user permissions.",
    },
    {
        "title": "Copy the User OAuth Token",
        "detail": "On Install App, copy the User OAuth Token (starts with xoxp-).",
    },
    {
        "title": "Generate an app-level token",
        "detail": "Under Basic Information, App-Level Tokens, generate a token with the "
        "connections:write scope (starts with xapp-). Only needed for the inbound stage, "
        "harmless to store now.",
    },
    {
        "title": "Paste the tokens below",
        "detail": "Paste each token into its field and save. Token values are never shown again.",
    },
    {
        "title": "Press Test connection",
        "detail": "The test reports your workspace and user when the user token works.",
    },
]


def _items(source: dict[str, str], kind: str) -> list[dict[str, str]]:
    return [{"name": name, "kind": kind, "reason": reason} for name, reason in source.items()]


def required_scopes() -> list[str]:
    return list(USER_SCOPES_REQUIRED)


def build_manifest(include_inbound: bool = True) -> dict[str, Any]:
    """Slack app manifest: static, free of tokens, relay IDs and any relay-record field."""
    user_scopes = required_scopes() + (list(USER_SCOPES_INBOUND) if include_inbound else [])
    manifest: dict[str, Any] = {
        "display_information": {
            "name": APP_NAME,
            "description": APP_DESCRIPTION,
            "background_color": APP_BACKGROUND_COLOR,
        },
        "oauth_config": {"scopes": {"user": user_scopes}},
        "settings": {
            "org_deploy_enabled": False,
            "socket_mode_enabled": include_inbound,
            "token_rotation_enabled": False,
        },
    }
    if BOT_SCOPES:
        manifest["oauth_config"]["scopes"]["bot"] = list(BOT_SCOPES)
    if include_inbound:
        manifest["settings"]["event_subscriptions"] = {"user_events": list(USER_EVENTS_INBOUND)}
    return manifest


def build_manifest_response(include_inbound: bool = True) -> dict[str, Any]:
    manifest = build_manifest(include_inbound)
    inbound = _items(USER_SCOPES_INBOUND, "scope") + _items(USER_EVENTS_INBOUND, "event")
    inbound.append({
        "name": "socket_mode",
        "kind": "setting",
        "reason": "Delivers Slack events to ccWebUI without a public URL.",
    })
    return {
        "manifest": manifest,
        "manifest_json": json.dumps(manifest, indent=2),
        "scopes": {
            "required": _items(USER_SCOPES_REQUIRED, "scope"),
            "optional": _items(USER_SCOPES_OPTIONAL, "scope"),
            "inbound": inbound,
        },
        "include_inbound": include_inbound,
        "steps": SETUP_STEPS,
    }
