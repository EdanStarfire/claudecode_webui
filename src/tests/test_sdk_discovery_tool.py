"""Tests for SDK discovery tool."""

import pytest
import asyncio
import tempfile
from unittest.mock import Mock, patch, AsyncMock
from pathlib import Path

from ..sdk_discovery_tool import SDKDiscoveryTool, DiscoveryScenario, DiscoveryResult
from ..message_parser import MessageType


class TestDiscoveryScenario:
    """Test cases for DiscoveryScenario dataclass."""

    def test_scenario_creation(self):
        """Test DiscoveryScenario creation."""
        scenario = DiscoveryScenario(
            name="test_scenario",
            description="Test scenario description",
            messages=["Hello", "World"],
            expected_types=["system", "assistant"]
        )

        assert scenario.name == "test_scenario"
        assert scenario.description == "Test scenario description"
        assert scenario.messages == ["Hello", "World"]
        assert scenario.expected_types == ["system", "assistant"]
        assert scenario.timeout == 30.0  # Default value

    def test_scenario_custom_timeout(self):
        """Test DiscoveryScenario with custom timeout."""
        scenario = DiscoveryScenario(
            name="test",
            description="Test",
            messages=["test"],
            expected_types=["system"],
            timeout=60.0
        )

        assert scenario.timeout == 60.0


class TestDiscoveryResult:
    """Test cases for DiscoveryResult dataclass."""

    def test_result_creation(self):
        """Test DiscoveryResult creation."""
        result = DiscoveryResult(
            scenario_name="test_scenario",
            success=True,
            messages_captured=5,
            message_types_seen={"system", "assistant"},
            raw_messages=[{"type": "system"}],
            parsed_messages=[],
            execution_time=2.5
        )

        assert result.scenario_name == "test_scenario"
        assert result.success is True
        assert result.messages_captured == 5
        assert result.message_types_seen == {"system", "assistant"}
        assert result.execution_time == 2.5
        assert result.error_message is None


class TestSDKDiscoveryTool:
    """Test cases for SDKDiscoveryTool class."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for testing."""
        with tempfile.TemporaryDirectory() as temp_dir:
            yield temp_dir

    @pytest.fixture
    def discovery_tool(self, temp_dir):
        """Create an SDKDiscoveryTool instance for testing."""
        return SDKDiscoveryTool(working_directory=temp_dir)

    def test_initialization(self, discovery_tool, temp_dir):
        """Test SDKDiscoveryTool initialization."""
        assert str(discovery_tool.working_directory) == temp_dir
        assert len(discovery_tool.scenarios) > 0
        assert discovery_tool.parser is not None
        assert len(discovery_tool.results) == 0

    def test_create_discovery_scenarios(self, discovery_tool):
        """Test discovery scenario creation."""
        scenarios = discovery_tool.scenarios

        assert len(scenarios) > 0

        # Check that we have expected scenario names
        scenario_names = [s.name for s in scenarios]
        assert "basic_interaction" in scenario_names
        assert "file_operation" in scenario_names
        assert "error_handling" in scenario_names  # Updated from "error_scenario"

        # Check that scenarios have required fields
        for scenario in scenarios:
            assert scenario.name
            assert scenario.description
            assert len(scenario.messages) > 0
            assert len(scenario.expected_types) > 0

    def test_list_scenarios(self, discovery_tool):
        """Test scenario listing."""
        scenarios = discovery_tool.list_scenarios()

        assert len(scenarios) > 0
        for scenario in scenarios:
            assert "name" in scenario
            assert "description" in scenario
            assert "message_count" in scenario
            assert "expected_types" in scenario

    @pytest.mark.asyncio
    async def test_run_single_scenario_nonexistent(self, discovery_tool):
        """Test running a nonexistent scenario."""
        result = await discovery_tool.run_single_scenario("nonexistent_scenario")

        assert result is None

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_run_scenario_basic(self, discovery_tool):
        """Test running a basic scenario with SDK."""
        scenario = DiscoveryScenario(
            name="test_scenario",
            description="Test scenario",
            messages=["Hello SDK"],
            expected_types=["system", "assistant"]
        )

        result = await discovery_tool._run_scenario(scenario)

        assert result.scenario_name == "test_scenario"
        assert result.success is True  # Should succeed with SDK
        assert result.messages_captured >= 0
        assert isinstance(result.message_types_seen, set)
        assert result.execution_time > 0

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_run_scenario_with_callbacks(self, discovery_tool):
        """Test scenario execution with message callbacks."""
        messages_captured = []

        # Mock the ClaudeSDK to capture callback behavior
        with patch('src.sdk_discovery_tool.ClaudeSDK') as mock_sdk_class:
            mock_sdk = Mock()
            # Use AsyncMock for async methods
            mock_sdk.start = AsyncMock(return_value=True)
            mock_sdk.send_message = AsyncMock(return_value=True)
            mock_sdk.terminate = AsyncMock(return_value=True)
            mock_sdk_class.return_value = mock_sdk

            scenario = DiscoveryScenario(
                name="callback_test",
                description="Test callbacks",
                messages=["Test message"],
                expected_types=["system"]
            )

            result = await discovery_tool._run_scenario(scenario)

            # Should have called the SDK constructor with callbacks
            mock_sdk_class.assert_called_once()
            call_kwargs = mock_sdk_class.call_args[1]
            assert "message_callback" in call_kwargs
            assert "error_callback" in call_kwargs

    def test_generate_summary_empty(self, discovery_tool):
        """Test summary generation with no results."""
        summary = discovery_tool._generate_summary(5.0)

        assert summary["sdk_discovery_info"]["total_scenarios"] == len(discovery_tool.scenarios)
        assert summary["sdk_discovery_info"]["successful_scenarios"] == 0
        assert summary["sdk_discovery_info"]["failed_scenarios"] == 0
        assert summary["sdk_discovery_info"]["total_execution_time"] == 5.0
        assert summary["message_types_discovered"]["total_unique_types"] == 0

    def test_generate_summary_with_results(self, discovery_tool):
        """Test summary generation with mock results."""
        # Add mock results
        discovery_tool.results = [
            DiscoveryResult(
                scenario_name="test1",
                success=True,
                messages_captured=3,
                message_types_seen={"system", "assistant"},
                raw_messages=[],
                parsed_messages=[],
                execution_time=1.5
            ),
            DiscoveryResult(
                scenario_name="test2",
                success=False,
                messages_captured=0,
                message_types_seen=set(),
                raw_messages=[],
                parsed_messages=[],
                error_message="Test error",
                execution_time=0.5
            )
        ]

        summary = discovery_tool._generate_summary(2.0)

        assert summary["sdk_discovery_info"]["successful_scenarios"] == 1
        assert summary["sdk_discovery_info"]["failed_scenarios"] == 1
        assert summary["sdk_discovery_info"]["total_messages_captured"] == 3
        assert summary["message_types_discovered"]["total_unique_types"] == 2
        assert "system" in summary["message_types_discovered"]["types"]
        assert "assistant" in summary["message_types_discovered"]["types"]

    def test_get_message_samples_empty(self, discovery_tool):
        """Test getting message samples with no results."""
        samples = discovery_tool.get_message_samples("system")

        assert samples == []

    def test_get_message_samples_with_data(self, discovery_tool):
        """Test getting message samples with mock data."""
        # Add mock results with raw messages
        discovery_tool.results = [
            DiscoveryResult(
                scenario_name="test",
                success=True,
                messages_captured=2,
                message_types_seen={"system", "assistant"},
                raw_messages=[
                    {"type": "system", "content": "System message 1"},
                    {"type": "assistant", "content": "Assistant message 1"},
                    {"type": "system", "content": "System message 2"}
                ],
                parsed_messages=[],
                execution_time=1.0
            )
        ]

        system_samples = discovery_tool.get_message_samples("system")
        assistant_samples = discovery_tool.get_message_samples("assistant")
        unknown_samples = discovery_tool.get_message_samples("unknown")

        assert len(system_samples) == 2
        assert len(assistant_samples) == 1
        assert len(unknown_samples) == 0
        assert system_samples[0]["content"] == "System message 1"
        assert assistant_samples[0]["content"] == "Assistant message 1"

    @pytest.mark.asyncio
    async def test_save_results_creates_directory(self, discovery_tool, temp_dir):
        """Test that save results creates necessary directories."""
        summary = {
            "sdk_discovery_info": {"total_scenarios": 1},
            "message_types_discovered": {"types": []},
            "scenario_results": [],
            "parser_statistics": {},
            "timestamp": 123456789
        }

        # Change working directory to temp_dir for this test
        discovery_tool.working_directory = Path(temp_dir)

        await discovery_tool._save_results(summary)

        # Check that directory was created (in current working directory, not temp_dir)
        data_dir = Path("data") / "sdk_discovery"
        assert data_dir.exists()

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_run_discovery_integration(self, temp_dir):
        """Test full discovery run integration (slow test - requires real SDK calls)."""
        discovery_tool = SDKDiscoveryTool(working_directory=temp_dir)

        # Run discovery with actual SDK
        summary = await discovery_tool.run_discovery(save_results=False)

        assert "sdk_discovery_info" in summary
        assert "message_types_discovered" in summary
        assert "scenario_results" in summary
        assert summary["sdk_discovery_info"]["total_scenarios"] > 0


class TestSDKDiscoveryMain:
    """Test cases for main function and CLI integration."""

    @pytest.mark.asyncio
    async def test_main_list_scenarios(self):
        """Test main function with --list argument."""
        # This would require mocking sys.argv and capturing output
        # For now, we'll test the list functionality directly
        discovery = SDKDiscoveryTool()
        scenarios = discovery.list_scenarios()

        assert len(scenarios) > 0
        for scenario in scenarios:
            assert "name" in scenario
            assert "description" in scenario