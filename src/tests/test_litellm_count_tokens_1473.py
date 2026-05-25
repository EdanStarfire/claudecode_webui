"""Slow live-flow regression tests for issue #1473.

Run with: uv run pytest -m slow src/tests/test_litellm_count_tokens_1473.py -v

These tests exercise the real LiteLLM proxy code path using httpx interception
rather than full network connectivity. They are skipped in normal CI (addopts
in pyproject.toml excludes the 'slow' marker).
"""

import types
from unittest.mock import patch

import httpx
import pytest

# Pre-import so the real module is in sys.modules before any test mocks litellm.
from litellm.llms.openai.responses.count_tokens.token_counter import OpenAITokenCounter
from litellm.proxy import proxy_server as _litellm_ps


@pytest.fixture(autouse=True)
def _restore_openai_counter():
    """Restore OpenAITokenCounter.should_use_token_counting_api after each test."""
    original = OpenAITokenCounter.should_use_token_counting_api
    yield
    OpenAITokenCounter.should_use_token_counting_api = original


def _apply_patch_directly(state: object) -> None:
    """Apply the #1473 monkey-patch directly to _litellm_ps.app.state.

    Mirrors the logic in LiteLLMProxyManager._launch_server to let slow tests
    exercise it without spinning up uvicorn.
    """
    if not getattr(state, "_openai_count_tokens_disabled", False):
        state._openai_count_tokens_original = OpenAITokenCounter.should_use_token_counting_api
        OpenAITokenCounter.should_use_token_counting_api = (
            lambda self, custom_llm_provider=None: False
        )
        state._openai_count_tokens_disabled = True


def _remove_patch_directly(state: object) -> None:
    if getattr(state, "_openai_count_tokens_disabled", False):
        OpenAITokenCounter.should_use_token_counting_api = state._openai_count_tokens_original
        state._openai_count_tokens_disabled = False


@pytest.mark.slow
@pytest.mark.asyncio
async def test_count_tokens_succeeds_without_calling_upstream_responses_api(tmp_path):
    """Patch prevents upstream POST to /v1/responses/input_tokens entirely.

    Flow:
    1. Apply the class-level patch (same logic as _launch_server).
    2. Call _try_provider_token_count with an OpenAITokenCounter and
       Anthropic-shaped tools / content (the exact shape that produced 400s).
    3. Assert the function returns None (local tiktoken fallback triggered).
    4. Assert zero outbound HTTP calls were recorded.
    """
    fake_state = types.SimpleNamespace()
    _apply_patch_directly(fake_state)

    recorded: list[str] = []

    async def recording_send(self, request, **kwargs):
        recorded.append(str(request.url))
        return httpx.Response(200, json={"input_tokens": 99})

    with patch.object(httpx.AsyncClient, "send", recording_send):
        counter = OpenAITokenCounter()
        result = await _litellm_ps._try_provider_token_count(
            provider_counter=counter,
            custom_llm_provider="openai",
            model_to_use="gpt-4o-mini",
            messages=[
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "Hello world"}],
                },
                {
                    "role": "assistant",
                    # Anthropic-shaped content block — rejected by Responses API
                    "content": [{"type": "text", "text": "Hi there"}],
                },
            ],
            contents=None,
            deployment={},
            request_model="gpt-4o-mini",
            tools=[
                # Anthropic-shaped tool — rejected by Responses API (missing type field)
                {
                    "name": "Read",
                    "description": "Read a file from disk.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                }
            ],
        )

    assert result is None, "Expected None (local fallback path), got a token count response"
    responses_calls = [u for u in recorded if "responses/input_tokens" in u]
    assert len(responses_calls) == 0, (
        f"Expected zero calls to /v1/responses/input_tokens; got: {responses_calls}"
    )


@pytest.mark.slow
@pytest.mark.asyncio
async def test_v1_messages_chat_still_routes_to_openai_after_patch(tmp_path):
    """CATASTROPHIC-FAILURE REGRESSION GUARD.

    The prior reverted fix (sidecar-based) broke all OpenAI-catalog /v1/messages
    traffic. This test confirms the patch touches ONLY OpenAITokenCounter (the
    count_tokens path); the chat-completion code path is entirely independent
    and still routes outbound.

    Flow:
    1. Apply the class-level patch.
    2. Attempt a real litellm.acompletion call with httpx intercepted.
    3. Assert the mock received at least one request to an OpenAI-compatible path.
    4. Assert the mock received ZERO requests to /v1/responses/input_tokens.
    """
    import litellm

    fake_state = types.SimpleNamespace()
    _apply_patch_directly(fake_state)

    recorded: list[str] = []

    async def recording_send(self, request, **kwargs):
        recorded.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Hello"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
                "model": "gpt-4o-mini",
            },
        )

    with patch.object(httpx.AsyncClient, "send", recording_send):
        await litellm.acompletion(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "Hello"}],
            api_key="fake-key-1473",
        )

    # At least one outbound request was made (chat completion path still active)
    assert len(recorded) >= 1, "Expected at least one outbound HTTP call for chat completion"

    request_urls = recorded
    assert any("/v1/" in u for u in request_urls), (
        f"Expected a /v1/* path among outbound calls; got: {request_urls}"
    )

    # No spurious count_tokens API calls
    responses_calls = [u for u in request_urls if "responses/input_tokens" in u]
    assert len(responses_calls) == 0, (
        f"Expected zero calls to /v1/responses/input_tokens; got: {responses_calls}"
    )
