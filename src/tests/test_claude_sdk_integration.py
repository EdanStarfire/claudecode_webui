"""Real Claude SDK integration tests with live API calls.

All tests are marked @pytest.mark.slow and excluded from default pytest runs.
Run explicitly with: uv run pytest src/tests/test_claude_sdk_integration.py -v -s
"""

import asyncio
import os
import uuid

import pytest

from ..claude_sdk import ClaudeSDK, SessionState


@pytest.fixture
def tmp_working_dir(tmp_path):
    """Provide a temporary working directory for SDK sessions."""
    return str(tmp_path)


def make_sdk(
    tmp_working_dir,
    *,
    message_callback=None,
    error_callback=None,
    permission_callback=None,
    permissions="acceptEdits",
    tools=None,
    model="sonnet",
    system_prompt="Reply in 10 words or fewer.",
):
    """Create a ClaudeSDK instance with sensible defaults for integration tests."""
    return ClaudeSDK(
        session_id=str(uuid.uuid4()),
        working_directory=tmp_working_dir,
        message_callback=message_callback,
        error_callback=error_callback,
        permission_callback=permission_callback,
        permissions=permissions,
        tools=tools if tools is not None else [],
        model=model,
        system_prompt=system_prompt,
    )


async def wait_for(predicate, timeout=30, interval=0.3):
    """Poll predicate until truthy or timeout."""
    elapsed = 0.0
    while elapsed < timeout:
        if predicate():
            return True
        await asyncio.sleep(interval)
        elapsed += interval
    return False


async def wait_for_running(sdk, timeout=30):
    """Wait until SDK reaches RUNNING state."""
    return await wait_for(lambda: sdk.info.state == SessionState.RUNNING, timeout=timeout)


@pytest.mark.slow
class TestClaudeSDKIntegration:
    """Integration tests that make real Claude API calls."""

    async def test_start_and_reach_running_state(self, tmp_working_dir):
        """SDK starts and reaches RUNNING state."""
        sdk = make_sdk(tmp_working_dir)
        try:
            result = await sdk.start()
            assert result is True
            reached = await wait_for_running(sdk)
            assert reached, f"SDK did not reach RUNNING state, got: {sdk.info.state}"
            assert sdk.info.state == SessionState.RUNNING
        finally:
            await sdk.terminate()

    async def test_send_message_and_stream_response(self, tmp_working_dir):
        """Sending a message produces at least one assistant response."""
        messages = []

        async def on_message(msg):
            messages.append(msg)

        sdk = make_sdk(tmp_working_dir, message_callback=on_message)
        try:
            await sdk.start()
            await wait_for_running(sdk)
            await sdk.send_message("Say hello")

            has_assistant = await wait_for(
                lambda: any(
                    m.get("type") == "assistant" or m.get("type") == "result"
                    for m in messages
                ),
                timeout=30,
            )
            assert has_assistant, f"No assistant message received. Got {len(messages)} messages: {[m.get('type') for m in messages]}"
        finally:
            await sdk.terminate()

    async def test_permission_callback_triggered(self, tmp_working_dir):
        """Permission callback fires when using default permissions with no allowed tools."""
        callback_calls = []

        def on_permission(tool_name, input_params, context):
            callback_calls.append({"tool": tool_name, "input": input_params})
            return {"behavior": "allow"}

        marker = uuid.uuid4().hex[:8]
        target_file = f"/tmp/sdk_test_perm_{marker}.txt"

        sdk = make_sdk(
            tmp_working_dir,
            permissions="default",
            tools=[],
            permission_callback=on_permission,
        )
        try:
            await sdk.start()
            await wait_for_running(sdk)
            await sdk.send_message(
                f"Run this bash command: echo 'test' > {target_file}"
            )

            triggered = await wait_for(lambda: len(callback_calls) > 0, timeout=30)
            assert triggered, "Permission callback was never called"
        finally:
            await sdk.terminate()
            # Clean up temp file
            try:
                os.unlink(target_file)
            except OSError:
                pass

    async def test_permission_callback_deny(self, tmp_working_dir):
        """Permission callback deny prevents tool execution."""
        callback_calls = []

        def on_permission(tool_name, input_params, context):
            callback_calls.append({"tool": tool_name})
            return {"behavior": "deny"}

        marker = uuid.uuid4().hex[:8]
        target_file = f"/tmp/sdk_test_deny_{marker}.txt"

        sdk = make_sdk(
            tmp_working_dir,
            permissions="default",
            tools=[],
            permission_callback=on_permission,
        )
        try:
            await sdk.start()
            await wait_for_running(sdk)
            await sdk.send_message(
                f"Run this bash command: echo 'denied' > {target_file}"
            )

            triggered = await wait_for(lambda: len(callback_calls) > 0, timeout=30)
            assert triggered, "Permission callback was never called"
            # Give a moment for any file creation
            await asyncio.sleep(2)
            assert not os.path.exists(target_file), "File was created despite deny"
        finally:
            await sdk.terminate()
            try:
                os.unlink(target_file)
            except OSError:
                pass

    @pytest.mark.timeout(60)
    async def test_interrupt_stops_processing(self, tmp_working_dir):
        """Interrupting a session stops response streaming."""
        messages = []

        async def on_message(msg):
            messages.append(msg)

        sdk = make_sdk(
            tmp_working_dir,
            message_callback=on_message,
            system_prompt="You are a verbose writer. Write very long detailed responses.",
        )
        try:
            await sdk.start()
            await wait_for_running(sdk)
            await sdk.send_message(
                "Write a 500-word essay about the history of programming languages."
            )

            # Wait for streaming to begin
            await wait_for(lambda: len(messages) > 0, timeout=15)
            await asyncio.sleep(1)

            count_before = len(messages)
            await sdk.interrupt_session()
            await asyncio.sleep(3)
            count_after = len(messages)

            # After interrupt, message growth should stop or slow significantly
            # Allow some slack for messages in-flight
            growth = count_after - count_before
            assert growth < 10, (
                f"Messages kept growing after interrupt: {count_before} -> {count_after} "
                f"(growth={growth})"
            )
        finally:
            await sdk.terminate()

    async def test_terminate_cleanup(self, tmp_working_dir):
        """Terminating sets state to TERMINATED and is_running returns False."""
        sdk = make_sdk(tmp_working_dir)
        try:
            await sdk.start()
            await wait_for_running(sdk)
            assert sdk.is_running() is True
        finally:
            result = await sdk.terminate()
            assert result is True
            assert sdk.info.state == SessionState.TERMINATED
            assert sdk.is_running() is False

    async def test_set_permission_mode(self, tmp_working_dir):
        """Permission mode can be changed at runtime."""
        sdk = make_sdk(tmp_working_dir, permissions="default")
        try:
            await sdk.start()
            await wait_for_running(sdk)
            result = await sdk.set_permission_mode("acceptEdits")
            assert result is True
            assert sdk.current_permission_mode == "acceptEdits"
        finally:
            await sdk.terminate()

    @pytest.mark.timeout(90)
    async def test_message_queue_ordering(self, tmp_working_dir):
        """Messages sent in rapid succession are processed in order."""
        messages = []

        async def on_message(msg):
            messages.append(msg)

        sdk = make_sdk(
            tmp_working_dir,
            message_callback=on_message,
            permissions="acceptEdits",
            system_prompt="Reply with ONLY the single letter requested. No other text.",
        )
        try:
            await sdk.start()
            await wait_for_running(sdk)

            await sdk.send_message("Say only: A")
            await sdk.send_message("Say only: B")
            await sdk.send_message("Say only: C")

            # Wait for responses to all 3 messages
            # Each message produces at least a user echo + assistant response
            # We need at least 3 assistant/result messages
            def result_count():
                return sum(
                    1 for m in messages
                    if m.get("type") in ("assistant", "result")
                )

            enough = await wait_for(lambda: result_count() >= 3, timeout=60)
            assert enough, (
                f"Expected >= 3 assistant/result messages, got {result_count()}. "
                f"Types: {[m.get('type') for m in messages]}"
            )

            # Verify ordering: extract assistant text content in order
            assistant_texts = []
            for m in messages:
                if m.get("type") == "assistant":
                    content = m.get("content", "")
                    if isinstance(content, list):
                        content = " ".join(
                            block.get("text", "") for block in content
                            if isinstance(block, dict) and block.get("type") == "text"
                        )
                    if content:
                        assistant_texts.append(content)

            # With sequential processing, A should appear before B, B before C
            full_text = " ||| ".join(assistant_texts)
            pos_a = full_text.find("A")
            pos_b = full_text.find("B")
            pos_c = full_text.find("C")

            if pos_a >= 0 and pos_b >= 0:
                assert pos_a < pos_b, f"A should come before B in responses. Text: {full_text}"
            if pos_b >= 0 and pos_c >= 0:
                assert pos_b < pos_c, f"B should come before C in responses. Text: {full_text}"
        finally:
            await sdk.terminate()

    async def test_error_handling_invalid_model(self, tmp_working_dir):
        """Invalid model name causes SDK to fail gracefully."""
        errors = []

        async def on_error(error_type, exception):
            errors.append({"type": error_type, "error": str(exception)})

        sdk = make_sdk(
            tmp_working_dir,
            model="nonexistent-model-xyz-9999",
            error_callback=on_error,
        )
        try:
            result = await sdk.start()
            if result:
                # SDK started but will fail on first query or during init
                await wait_for_running(sdk, timeout=10)
                # Try sending a message to trigger the model error
                await sdk.send_message("Hello")
                # Wait for failure
                failed = await wait_for(
                    lambda: sdk.info.state == SessionState.FAILED or len(errors) > 0,
                    timeout=30,
                )
                assert failed or sdk.info.state == SessionState.FAILED, (
                    f"Expected FAILED state with invalid model, got: {sdk.info.state}"
                )
            else:
                # start() returned False — immediate failure
                assert sdk.info.state == SessionState.FAILED
                assert sdk.info.error_message is not None
        finally:
            await sdk.terminate()

    async def test_double_terminate_is_safe(self, tmp_working_dir):
        """Calling terminate twice does not raise."""
        sdk = make_sdk(tmp_working_dir)
        try:
            await sdk.start()
            await wait_for_running(sdk)
        finally:
            await sdk.terminate()
            # Second terminate should be safe
            await sdk.terminate()
            assert sdk.info.state == SessionState.TERMINATED
