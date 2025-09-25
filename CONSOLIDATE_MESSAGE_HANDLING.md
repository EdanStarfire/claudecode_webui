# Message Handling Consolidation Implementation Plan

## Overview

This document outlines the plan to consolidate message handling across the Claude Code WebUI to eliminate inconsistencies between real-time message processing and session restoration. The goal is to ensure consistent message parsing, filtering, and display regardless of whether messages arrive live or are loaded from storage.

## Problem Summary

Currently, there are two distinct message processing streams:
1. **Real-time**: SDK → SessionCoordinator callback → MessageParser → Storage + WebSocket
2. **Historical**: Storage → MessageParser (again) → WebUI rendering

This creates inconsistencies where:
- Messages are parsed differently in different contexts
- Tool calls are handled via different logic paths
- Raw SDK data is used directly in business logic (violating separation of concerns)
- Same messages display differently based on loading source

## Core Principles

1. **Raw SDK Data Preservation**: `raw_sdk_message` must be preserved for debugging and SDK evolution tracking
2. **Business Logic Separation**: Application logic should NEVER directly access raw SDK data
3. **Single Source of Truth**: One message processing pipeline for both real-time and historical messages
4. **Complete Data Extraction**: All business-relevant data extracted upfront during initial processing

---

## Phase 1: Analysis & Documentation

### [ ] Task 1.1: Complete Raw SDK Usage Audit
- [ ] Search codebase for all `sdk_message` access patterns
- [ ] Identify all `raw_sdk_response` string parsing locations
- [ ] Document each location where raw SDK data is used in business logic
- [ ] Map to files and line numbers for systematic refactoring
- [ ] Store collected details in `CMH_Task_1.1_ANALYSIS.md

**Files to audit:**
- [ ] `src/message_parser.py` (all handlers)
- [ ] `src/session_coordinator.py` (message callbacks)
- [ ] `src/web_server.py` (message serialization)
- [ ] `static/app.js` (WebUI message processing)
- [ ] any other apps / libraries where used that might affect the behavior

### [ ] Task 1.2: Document Current Business Data Extraction
- [ ] Catalog all metadata fields currently extracted
- [ ] Document tool use data structures
- [ ] Map thinking block extraction patterns
- [ ] Identify permission request/response handling
- [ ] Note tool result processing logic
- [ ] Store collected details in `CMH_Task_1.2_ANALYSIS.md

### [ ] Task 1.3: Identify Dual Processing Paths
- [ ] Map real-time message flow from SDK to display
- [ ] Map historical message flow from storage to display
- [ ] Document differences in processing logic
- [ ] Identify inconsistencies in tool call handling
- [ ] Note WebUI rendering differences
- [ ] Store collected details in `CMH_Task_1.3_ANALYSIS.md

### [ ] Task 1.4: Create Standardized Metadata Schema Design
- [ ] Design comprehensive metadata structure for all message types
- [ ] Define tool use metadata format
- [ ] Specify thinking block metadata structure
- [ ] Plan permission handling metadata
- [ ] Include all business data needed by WebUI
- [ ] Store collected details in `CMH_Task_1.4_SCHEMA_DESIGN.md

**Target Schema Structure:**
```json
{
  "type": "assistant|user|system|result|permission_request|permission_response",
  "content": "extracted text content",
  "metadata": {
    "tool_uses": [{"id": "...", "name": "...", "input": {...}}],
    "tool_results": [{"tool_use_id": "...", "content": "...", "is_error": false}],
    "thinking_content": "extracted thinking text",
    "thinking_blocks": [{"content": "...", "timestamp": "..."}],
    "permission_requests": [...],
    "has_tool_uses": boolean,
    "has_thinking": boolean,
    "has_tool_results": boolean,
    "subtype": "init|client_launched|interrupt|etc",
    // All other business-critical data
  },
  "raw_sdk_message": {...},  // Preserved but unused in business logic
  "timestamp": "ISO string",
  "session_id": "string"
}
```

---
NOTE: Use output markdown files from Phase 1 to drive the details of the below items.
IMPORTANT! If a task's todo lists in subsequent phases need to be updated or changed based on the analyses above, proactively update these tasks below with the change before beginning the task.


## Phase 2: Data Extraction Standardization

### [ ] Task 2.1: Refactor MessageParser Base Classes
- [ ] Remove all direct `sdk_message` object access from handlers
- [ ] Create `_extract_business_data()` method for each handler
- [ ] Implement comprehensive data extraction upfront
- [ ] Store all business data in standardized metadata
- [ ] Preserve `raw_sdk_message` for debugging only

**Implementation checklist:**
- [ ] `SystemMessageHandler`: Extract subtype, session info, error details
- [ ] `AssistantMessageHandler`: Extract text, tool uses, thinking blocks
- [ ] `UserMessageHandler`: Extract text, tool results, tool uses
- [ ] `ResultMessageHandler`: Extract completion data, usage stats
- [ ] `PermissionRequestHandler`: Extract tool name, parameters, request ID
- [ ] `PermissionResponseHandler`: Extract decision, reasoning, timing

### [ ] Task 2.2: Eliminate String Parsing of Raw SDK Responses
- [ ] Remove regex parsing of `"[ThinkingBlock(...)]"` patterns
- [ ] Remove regex parsing of `"[ToolUseBlock(...)]"` patterns
- [ ] Remove regex parsing of `"[ToolResultBlock(...)]"` patterns
- [ ] Replace with proper SDK object processing during initial extraction
- [ ] Handle both live SDK objects and historical raw data consistently

### [ ] Task 2.3: Standardize Raw SDK Message Capture
- [ ] Ensure ALL SDK messages store `raw_sdk_message` field
- [ ] Verify consistent field naming across all message types
- [ ] Maintain backward compatibility with existing `raw_sdk_response`
- [ ] Add migration logic for legacy message formats
- [ ] Preserve complete SDK data for future debugging

### [ ] Task 2.4: Update Business Logic to Use Metadata Only
- [ ] Refactor all message processing to use extracted metadata
- [ ] Remove dependencies on raw SDK object structure
- [ ] Update tool call management to use metadata
- [ ] Modify permission handling to use extracted data
- [ ] Ensure thinking block processing uses metadata

---

## Phase 3: Processing Pipeline Unification

### [ ] Task 3.1: Create Unified MessageProcessor Class
- [ ] Design new `MessageProcessor` class in `src/message_processor.py`
- [ ] Implement single entry point for all message processing
- [ ] Handle both SDK objects and stored JSON consistently
- [ ] Route all messages through same processing logic
- [ ] Maintain backward compatibility

**MessageProcessor Interface:**
```python
class MessageProcessor:
    def __init__(self, message_parser: MessageParser):
        self.parser = message_parser

    def process_message(self, message_data: Dict[str, Any], source: str = "sdk") -> ParsedMessage:
        """Process message from any source (SDK, storage) consistently"""

    def prepare_for_storage(self, parsed_message: ParsedMessage) -> Dict[str, Any]:
        """Prepare message for storage with all metadata"""

    def prepare_for_websocket(self, parsed_message: ParsedMessage) -> Dict[str, Any]:
        """Prepare message for WebSocket transmission"""
```

### [ ] Task 3.2: Refactor SessionCoordinator Message Handling
- [ ] Replace `_create_message_callback()` to use MessageProcessor
- [ ] Update `get_session_messages()` to use unified processing
- [ ] Remove dual parsing logic
- [ ] Ensure both real-time and historical use same pipeline
- [ ] Maintain storage and WebSocket callback functionality

### [ ] Task 3.3: Update Data Storage Integration
- [ ] Modify storage to save fully processed messages
- [ ] Ensure consistent metadata structure in storage
- [ ] Add migration for existing message formats
- [ ] Preserve raw SDK data in storage
- [ ] Update message retrieval to use processed format

### [ ] Task 3.4: Standardize WebSocket Message Format
- [ ] Define consistent WebSocket message structure
- [ ] Use processed message metadata for transmission
- [ ] Remove raw SDK object serialization issues
- [ ] Ensure real-time messages match historical format
- [ ] Update WebUI to expect standardized format

---

## Phase 4: WebUI Consolidation

### [ ] Task 4.1: Unify Message Processing Paths
- [ ] Consolidate `handleIncomingMessage()` and `renderMessages()` logic
- [ ] Create single `processMessage()` function for all message sources
- [ ] Remove dual processing paths
- [ ] Ensure consistent message filtering
- [ ] Standardize message display logic

**Target unified processing:**
```javascript
processMessage(message, source = 'websocket') {
    // Same processing logic regardless of source
    // Use metadata for all business decisions
    // Consistent tool call handling
    // Same filtering rules
    // Identical display formatting
}
```

### [ ] Task 4.2: Consolidate Tool Call Management
- [ ] Remove two-pass tool call processing in `renderMessages()`
- [ ] Update ToolCallManager to use standardized metadata
- [ ] Ensure same tool state tracking for real-time and historical
- [ ] Eliminate separate tool restoration logic
- [ ] Use consistent tool call display

### [ ] Task 4.3: Standardize Message Filtering
- [ ] Consolidate filtering logic in `shouldDisplayMessage()`
- [ ] Use metadata fields instead of raw message inspection
- [ ] Ensure consistent filtering for both message sources
- [ ] Remove duplicate filtering code
- [ ] Maintain current filtering behavior

### [ ] Task 4.4: Update Message Display Components
- [ ] Ensure consistent message rendering
- [ ] Use standardized metadata for all display decisions
- [ ] Remove raw data dependencies in UI
- [ ] Maintain current visual formatting
- [ ] Ensure tool calls display identically

---

## Phase 5: Testing & Validation

### [ ] Task 5.1: Message Consistency Validation
- [ ] Test real-time message processing vs historical loading
- [ ] Verify identical message structure and content
- [ ] Ensure same metadata availability in both scenarios
- [ ] Validate tool call information consistency
- [ ] Check thinking block display consistency

### [ ] Task 5.2: Tool Call Handling Verification
- [ ] Test tool call lifecycle in real-time scenarios
- [ ] Verify tool call restoration from historical messages
- [ ] Ensure identical tool state management
- [ ] Validate permission request/response handling
- [ ] Check tool result processing consistency

### [ ] Task 5.3: Raw SDK Data Preservation Testing
- [ ] Verify `raw_sdk_message` is preserved for all message types
- [ ] Ensure no business logic accesses raw SDK data
- [ ] Test debugging capabilities with preserved raw data
- [ ] Validate backward compatibility with existing messages
- [ ] Confirm future SDK evolution support

### [ ] Task 5.4: Performance and Reliability Testing
- [ ] Test message processing performance with unified pipeline
- [ ] Verify no processing loops or duplicate parsing
- [ ] Ensure session restoration performance
- [ ] Test WebSocket message handling reliability
- [ ] Validate storage/retrieval consistency

### [ ] Task 5.5: Integration Testing
- [ ] Test complete session lifecycle (create → use → restore)
- [ ] Verify message consistency across session state changes
- [ ] Test error handling in unified pipeline
- [ ] Validate interruption and permission handling
- [ ] Ensure no regressions in existing functionality

---

## Implementation Notes

### Backward Compatibility
- Maintain support for existing `raw_sdk_response` fields during transition
- Provide migration logic for legacy message formats
- Ensure gradual rollout without breaking existing sessions

### Performance Considerations
- Minimize processing overhead with single-pass parsing
- Cache extracted metadata to avoid re-processing
- Optimize storage format for fast retrieval

### Error Handling
- Graceful degradation when raw SDK format changes
- Robust handling of malformed or incomplete messages
- Clear error reporting for debugging

### Testing Strategy
- Unit tests for each MessageProcessor component
- Integration tests for real-time vs historical consistency
- End-to-end tests for complete session workflows
- Performance benchmarks for message processing

---

## Success Criteria

✅ **Consistency**: Messages behave identically whether received real-time or loaded from storage

✅ **Separation of Concerns**: Business logic completely independent of raw SDK format

✅ **Single Source of Truth**: One processing pipeline handles all messages

✅ **Maintainability**: Changes to message handling only need to be made in one place

✅ **Future-Proof**: SDK format changes don't break application functionality

✅ **Debuggability**: Raw SDK data preserved for troubleshooting and evolution tracking