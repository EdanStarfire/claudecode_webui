"""Reconstruct real claude_agent_sdk dataclass instances from SessionRecorder's
raw_log.jsonl capture format (_type + data = dataclasses.asdict() output).

Issue #1998 (stage 1a): the inverse of SessionRecorder.record_sdk_message().
Used by mock_sdk.py's raw-layer replay mode (AC6) to drive genuinely-typed SDK
objects through ClaudeSDK's real _process_sdk_message()/_convert_sdk_message()
path — those methods do isinstance() checks and re-call dataclasses.asdict(),
so a plain dict replay (the old fixture format) isn't sufficient here.

Only two message fields anywhere in the SDK's dataclass graph nest another
dataclass: ResultMessage.deferred_tool_use and RateLimitEvent.rate_limit_info
(both unambiguous, single-type). The one genuinely ambiguous case is
UserMessage/AssistantMessage.content: list[ContentBlock], a union of six
dataclasses that dataclasses.asdict() flattens to plain dicts with no type
tag — disambiguated below by field-key shape, with a name-based tie-break for
the one pair (ToolUseBlock vs ServerToolUseBlock) that shares an identical
key set.
"""

from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ConversationResetMessage,
    DeferredToolUse,
    HookEventMessage,
    MirrorErrorMessage,
    RateLimitEvent,
    RateLimitInfo,
    ResultError,
    ResultMessage,
    ServerToolResultBlock,
    ServerToolUseBlock,
    StreamEvent,
    SystemMessage,
    TaskNotificationMessage,
    TaskProgressMessage,
    TaskStartedMessage,
    TaskUpdatedMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

# Known ServerToolName literal values (claude_agent_sdk.types.ServerToolName) — the only
# signal that distinguishes a ServerToolUseBlock from a ToolUseBlock once both have been
# flattened to {"id", "name", "input"} by dataclasses.asdict().
_SERVER_TOOL_NAMES = {
    "advisor",
    "web_search",
    "web_fetch",
    "code_execution",
    "bash_code_execution",
    "text_editor_code_execution",
    "tool_search_tool_regex",
    "tool_search_tool_bm25",
}

_MESSAGE_TYPES: dict[str, type] = {
    cls.__name__: cls
    for cls in (
        UserMessage,
        AssistantMessage,
        SystemMessage,
        TaskStartedMessage,
        TaskProgressMessage,
        TaskNotificationMessage,
        TaskUpdatedMessage,
        MirrorErrorMessage,
        HookEventMessage,
        ResultMessage,
        StreamEvent,
        RateLimitEvent,
        ConversationResetMessage,
    )
}


def _reconstruct_content_block(block: dict[str, Any]) -> Any:
    keys = set(block.keys())
    if keys == {"text"}:
        return TextBlock(text=block["text"])
    if keys == {"thinking", "signature"}:
        return ThinkingBlock(thinking=block["thinking"], signature=block["signature"])
    if keys == {"id", "name", "input"}:
        if block.get("name") in _SERVER_TOOL_NAMES:
            return ServerToolUseBlock(id=block["id"], name=block["name"], input=block["input"])
        return ToolUseBlock(id=block["id"], name=block["name"], input=block["input"])
    if keys == {"tool_use_id", "content", "is_error"}:
        return ToolResultBlock(
            tool_use_id=block["tool_use_id"],
            content=block["content"],
            is_error=block["is_error"],
        )
    if keys == {"tool_use_id", "content"}:
        return ServerToolResultBlock(tool_use_id=block["tool_use_id"], content=block["content"])
    raise ValueError(f"Unrecognized content block shape in raw_log: {sorted(keys)}")


def reconstruct_sdk_message(_type: str, data: dict[str, Any]) -> Any:
    """Reverse of SessionRecorder.record_sdk_message(): _type + asdict() data -> real object.

    Raises ValueError for a raw_log entry naming an SDK type this codebase doesn't
    recognize (e.g. a newer SDK version's message type) — callers should treat that
    as a fixture-vs-installed-SDK drift signal (see AC5's version-drift test) rather
    than silently skip it.
    """
    if _type == "ResultError":
        return ResultError(
            data.get("message", ""),
            data=data.get("data"),
            exit_code=data.get("exit_code"),
        )

    cls = _MESSAGE_TYPES.get(_type)
    if cls is None:
        raise ValueError(f"Unknown SDK message type in raw_log: {_type}")

    kwargs = dict(data)
    if cls in (UserMessage, AssistantMessage) and isinstance(kwargs.get("content"), list):
        kwargs["content"] = [_reconstruct_content_block(b) for b in kwargs["content"]]
    if cls is ResultMessage and kwargs.get("deferred_tool_use") is not None:
        kwargs["deferred_tool_use"] = DeferredToolUse(**kwargs["deferred_tool_use"])
    if cls is RateLimitEvent and kwargs.get("rate_limit_info") is not None:
        kwargs["rate_limit_info"] = RateLimitInfo(**kwargs["rate_limit_info"])
    return cls(**kwargs)
