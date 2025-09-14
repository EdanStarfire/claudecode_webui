"""Discovery tool for cataloging Claude Code SDK message formats."""

import asyncio
import json
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Set
from dataclasses import dataclass, asdict
import uuid

from .claude_sdk import ClaudeSDK
from .message_parser import MessageParser, ParsedMessage
from .logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class DiscoveryScenario:
    """A test scenario for discovering Claude Code SDK message formats."""
    name: str
    description: str
    messages: List[str]
    expected_types: List[str]
    timeout: float = 30.0


@dataclass
class DiscoveryResult:
    """Result of a discovery scenario execution."""
    scenario_name: str
    success: bool
    messages_captured: int
    message_types_seen: Set[str]
    raw_messages: List[Dict[str, Any]]
    parsed_messages: List[ParsedMessage]
    error_message: Optional[str] = None
    execution_time: float = 0.0


class SDKDiscoveryTool:
    """
    Tool for discovering and cataloging Claude Code SDK message formats.

    Runs various scenarios to capture different types of messages
    and builds a comprehensive library of message types and formats.
    """

    def __init__(self, working_directory: str = None):
        """
        Initialize the SDK discovery tool.

        Args:
            working_directory: Directory for running test sessions
        """
        self.working_directory = Path(working_directory or ".")
        self.parser = MessageParser()
        self.results: List[DiscoveryResult] = []

        # Create discovery scenarios
        self.scenarios = self._create_discovery_scenarios()

        logger.info(f"SDKDiscoveryTool initialized with {len(self.scenarios)} scenarios")

    def _create_discovery_scenarios(self) -> List[DiscoveryScenario]:
        """Create a comprehensive set of discovery scenarios for SDK."""
        return [
            DiscoveryScenario(
                name="basic_interaction",
                description="Basic user message and response via SDK",
                messages=["Hello, can you help me with a simple task?"],
                expected_types=["system", "assistant", "result"]
            ),
            DiscoveryScenario(
                name="thinking_scenario",
                description="Complex problem that should trigger thinking blocks",
                messages=["Solve this math problem step by step: What is 17 * 23 + 89? Show your reasoning process."],
                expected_types=["system", "assistant", "thinking", "result"]
            ),
            DiscoveryScenario(
                name="file_operation",
                description="File operation request via SDK",
                messages=["List the files in the current directory and show me their contents"],
                expected_types=["system", "assistant", "tool_use", "tool_result", "result"]
            ),
            DiscoveryScenario(
                name="code_creation_with_tools",
                description="Code creation task that requires tool usage",
                messages=["Create a Python script that calculates the factorial of 5, save it as factorial.py, then run it"],
                expected_types=["system", "assistant", "thinking", "tool_use", "tool_result", "result"]
            ),
            DiscoveryScenario(
                name="complex_analysis",
                description="Complex analysis that should trigger thinking and tools",
                messages=["Analyze the current directory structure, create a report of all Python files and their line counts"],
                expected_types=["system", "assistant", "thinking", "tool_use", "tool_result", "result"],
                timeout=60.0
            ),
            DiscoveryScenario(
                name="error_handling",
                description="Scenario that will trigger tool errors",
                messages=["Try to read the contents of a file called 'nonexistent_file_123456.txt'"],
                expected_types=["system", "assistant", "tool_use", "tool_result", "error", "result"]
            ),
            DiscoveryScenario(
                name="multi_tool_workflow",
                description="Complex workflow requiring multiple tools",
                messages=["Create a directory called 'sdk_test', write a hello world script in it, make it executable, and run it"],
                expected_types=["system", "assistant", "thinking", "tool_use", "tool_result", "result"],
                timeout=60.0
            ),
            DiscoveryScenario(
                name="reasoning_with_calculations",
                description="Problem requiring step-by-step reasoning",
                messages=["If I have 3 boxes with 7 items each, and I remove 2 items from the first box and add 5 items to the second box, how many items do I have total? Think through this step by step."],
                expected_types=["system", "assistant", "thinking", "result"]
            )
        ]

    async def run_discovery(self, save_results: bool = True) -> Dict[str, Any]:
        """
        Run all discovery scenarios and collect results.

        Args:
            save_results: Whether to save results to file

        Returns:
            Discovery summary with statistics and findings
        """
        logger.info(f"Starting SDK discovery with {len(self.scenarios)} scenarios")
        start_time = time.time()

        results = []
        for scenario in self.scenarios:
            logger.info(f"Running SDK scenario: {scenario.name}")
            result = await self._run_scenario(scenario)
            results.append(result)

            if result.success:
                logger.info(f"SDK scenario '{scenario.name}' completed: "
                          f"{result.messages_captured} messages captured")
            else:
                logger.error(f"SDK scenario '{scenario.name}' failed: {result.error_message}")

        self.results = results
        total_time = time.time() - start_time

        # Generate summary
        summary = self._generate_summary(total_time)

        if save_results:
            await self._save_results(summary)

        logger.info(f"SDK discovery completed in {total_time:.2f}s")
        return summary

    async def _run_scenario(self, scenario: DiscoveryScenario) -> DiscoveryResult:
        """Run a single discovery scenario."""
        messages = []
        session_id = str(uuid.uuid4())
        start_time = time.time()

        logger.info(f"Running SDK scenario {scenario.name} with session ID: {session_id}")

        def message_callback(message_data: Dict[str, Any]) -> None:
            messages.append(message_data)
            logger.debug(f"Captured SDK message: {message_data.get('type', 'unknown')}")

        def error_callback(error_type: str, exception: Exception) -> None:
            logger.warning(f"SDK error in scenario {scenario.name}: {error_type} - {exception}")

        # Use first message as initial message
        initial_message = scenario.messages[0] if scenario.messages else "Hello, can you help me?"

        sdk_session = ClaudeSDK(
            session_id=session_id,
            working_directory=str(self.working_directory),
            message_callback=message_callback,
            error_callback=error_callback,
            initial_prompt=initial_message
        )

        try:
            # Start the SDK session
            if not await sdk_session.start():
                return DiscoveryResult(
                    scenario_name=scenario.name,
                    success=False,
                    messages_captured=0,
                    message_types_seen=set(),
                    raw_messages=[],
                    parsed_messages=[],
                    error_message="Failed to start Claude Code SDK session",
                    execution_time=time.time() - start_time
                )

            # Send the message to SDK
            logger.info(f"Sending message to SDK: {initial_message[:50]}...")

            success = await sdk_session.send_message(initial_message)
            if not success:
                logger.warning("Failed to send message to SDK")

            # Wait a bit for SDK to process
            await asyncio.sleep(2.0)

            logger.info(f"SDK scenario {scenario.name} captured {len(messages)} messages")

            # Parse all captured messages
            parsed_messages = []
            message_types_seen = set()

            for message_data in messages:
                try:
                    parsed = self.parser.parse_message(message_data)
                    parsed_messages.append(parsed)
                    message_types_seen.add(message_data.get("type", "unknown"))
                except Exception as e:
                    logger.warning(f"Failed to parse message: {e}")

            execution_time = time.time() - start_time

            return DiscoveryResult(
                scenario_name=scenario.name,
                success=True,
                messages_captured=len(messages),
                message_types_seen=message_types_seen,
                raw_messages=messages,
                parsed_messages=parsed_messages,
                execution_time=execution_time
            )

        except Exception as e:
            logger.error(f"Error running SDK scenario {scenario.name}: {e}")
            return DiscoveryResult(
                scenario_name=scenario.name,
                success=False,
                messages_captured=len(messages),
                message_types_seen=set(message_data.get("type", "unknown") for message_data in messages),
                raw_messages=messages,
                parsed_messages=[],
                error_message=str(e),
                execution_time=time.time() - start_time
            )

        finally:
            # Always terminate the SDK session
            await sdk_session.terminate()

    def _generate_summary(self, total_time: float) -> Dict[str, Any]:
        """Generate a summary of SDK discovery results."""
        successful_scenarios = [r for r in self.results if r.success]
        failed_scenarios = [r for r in self.results if not r.success]

        # Collect all message types seen
        all_types_seen = set()
        total_messages = 0
        for result in self.results:
            all_types_seen.update(result.message_types_seen)
            total_messages += result.messages_captured

        # Get parser statistics
        parser_stats = self.parser.get_stats()

        summary = {
            "sdk_discovery_info": {
                "total_scenarios": len(self.scenarios),
                "successful_scenarios": len(successful_scenarios),
                "failed_scenarios": len(failed_scenarios),
                "total_execution_time": total_time,
                "total_messages_captured": total_messages
            },
            "message_types_discovered": {
                "total_unique_types": len(all_types_seen),
                "types": sorted(list(all_types_seen)),
                "unknown_types": parser_stats.get("unknown_types_seen", [])
            },
            "scenario_results": [
                {
                    "name": result.scenario_name,
                    "success": result.success,
                    "messages_captured": result.messages_captured,
                    "types_seen": sorted(list(result.message_types_seen)),
                    "execution_time": result.execution_time,
                    "error_message": result.error_message
                }
                for result in self.results
            ],
            "parser_statistics": parser_stats,
            "timestamp": time.time()
        }

        return summary

    async def _save_results(self, summary: Dict[str, Any]) -> None:
        """Save SDK discovery results to files."""
        try:
            # Create data directory
            data_dir = Path("data/sdk_discovery")
            data_dir.mkdir(parents=True, exist_ok=True)

            timestamp = int(time.time())

            # Save summary
            summary_file = data_dir / f"sdk_discovery_summary_{timestamp}.json"
            with open(summary_file, 'w', encoding='utf-8') as f:
                json.dump(summary, f, indent=2, default=str)

            # Save detailed raw data
            detailed_data = {
                "scenarios": [asdict(scenario) for scenario in self.scenarios],
                "results": []
            }

            for result in self.results:
                result_data = asdict(result)
                # Convert sets to lists for JSON serialization
                result_data["message_types_seen"] = list(result_data["message_types_seen"])
                # Convert ParsedMessage objects to dicts
                result_data["parsed_messages"] = [
                    asdict(message) for message in result.parsed_messages
                ]
                detailed_data["results"].append(result_data)

            detailed_file = data_dir / f"sdk_discovery_detailed_{timestamp}.json"
            with open(detailed_file, 'w', encoding='utf-8') as f:
                json.dump(detailed_data, f, indent=2, default=str)

            logger.info(f"SDK discovery results saved to {summary_file} and {detailed_file}")

        except Exception as e:
            logger.error(f"Failed to save SDK discovery results: {e}")

    async def run_single_scenario(self, scenario_name: str) -> Optional[DiscoveryResult]:
        """Run a single scenario by name."""
        scenario = next((s for s in self.scenarios if s.name == scenario_name), None)
        if not scenario:
            logger.error(f"SDK scenario not found: {scenario_name}")
            return None

        logger.info(f"Running single SDK scenario: {scenario_name}")
        return await self._run_scenario(scenario)

    def list_scenarios(self) -> List[Dict[str, Any]]:
        """List all available SDK scenarios."""
        return [
            {
                "name": scenario.name,
                "description": scenario.description,
                "message_count": len(scenario.messages),
                "expected_types": scenario.expected_types
            }
            for scenario in self.scenarios
        ]

    def get_message_samples(self, message_type: str) -> List[Dict[str, Any]]:
        """Get sample raw messages of a specific type."""
        samples = []
        for result in self.results:
            for message in result.raw_messages:
                if message.get("type") == message_type:
                    samples.append(message)
        return samples


# Command-line interface for running SDK discovery
async def main():
    """Main function for running SDK discovery from command line."""
    import argparse

    parser = argparse.ArgumentParser(description="Claude Code SDK Discovery Tool")
    parser.add_argument("--scenario", help="Run specific scenario")
    parser.add_argument("--list", action="store_true", help="List available scenarios")
    parser.add_argument("--working-dir", help="Working directory for sessions")

    args = parser.parse_args()

    # Set up logging
    from .logging_config import setup_logging
    setup_logging(log_level="DEBUG", enable_console=True)

    discovery = SDKDiscoveryTool(working_directory=args.working_dir)

    if args.list:
        scenarios = discovery.list_scenarios()
        print("\nAvailable SDK scenarios:")
        for scenario in scenarios:
            print(f"  {scenario['name']}: {scenario['description']}")
            print(f"    Messages: {scenario['message_count']}, "
                  f"Expected types: {', '.join(scenario['expected_types'])}")
        return

    if args.scenario:
        result = await discovery.run_single_scenario(args.scenario)
        if result:
            print(f"\nSDK scenario '{result.scenario_name}' results:")
            print(f"  Success: {result.success}")
            print(f"  Messages captured: {result.messages_captured}")
            print(f"  Types seen: {', '.join(result.message_types_seen)}")
        else:
            print(f"SDK scenario '{args.scenario}' not found")
        return

    # Run full SDK discovery
    summary = await discovery.run_discovery()
    print(f"\nSDK discovery completed!")
    print(f"  Scenarios: {summary['sdk_discovery_info']['successful_scenarios']}/"
          f"{summary['sdk_discovery_info']['total_scenarios']} successful")
    print(f"  Message types discovered: {summary['message_types_discovered']['total_unique_types']}")
    print(f"  Total messages: {summary['sdk_discovery_info']['total_messages_captured']}")


if __name__ == "__main__":
    asyncio.run(main())