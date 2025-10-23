# Backend Message Formats - Reference

This document catalogs the actual message formats found in `test_data/sessions/*/messages.jsonl` to guide frontend implementation.

## Timestamp Formats

Backend sends **TWO different formats**:

1. **ISO 8601 String** (User messages, System messages):
   ```json
   "timestamp": "2025-10-21T18:59:02.228585+00:00"
   ```

2. **Unix Timestamp in Seconds** (Assistant messages, Tool messages):
   ```json
   "timestamp": 1761073198.2721586
   ```

**Solution**: Our `utils/time.js` handles both formats automatically.

## Message Type: System

```json
{
  "type": "system",
  "content": "Claude Code Launched",
  "timestamp": "2025-10-22T22:42:24.874251+00:00",
  "metadata": {
    "subtype": "client_launched",
    "session_id": "05e06a0c-cff9-4c22-9560-de519616dc0e",
    "working_directory": null,
    "permissions": null,
    "tools": [],
    "model": null,
    "system_prompt": null,
    "has_tool_uses": false,
    "has_thinking": false,
    "has_tool_results": false
  },
  "session_id": "05e06a0c-cff9-4c22-9560-de519616dc0e"
}
```

**Display Rules**:
- Hide if `metadata.subtype === "init"` or `"client_launched"`
- Show minimal display for other subtypes

## Message Type: User

### Simple User Message
```json
{
  "type": "user",
  "content": "Welcome to 21 Questions! I've selected a common household object...",
  "session_id": "0801e1da-1f96-4461-b296-39460b58ea78",
  "timestamp": "2025-10-21T18:59:02.228585+00:00"
}
```

### User Message with Tool Results
```json
{
  "type": "user",
  "content": "Tool results: 1 results",
  "timestamp": 1761073198.3302684,
  "metadata": {
    "tool_uses": [],
    "tool_results": [
      {
        "tool_use_id": "toolu_01SMt2ETHn8ocNGdRXJaWaWF",
        "content": "Message sent to fred",
        "is_error": null,
        "timestamp": 1761073198.3302684
      }
    ],
    "has_tool_uses": false,
    "has_tool_results": true,
    "has_thinking": false
  },
  "session_id": "0801e1da-1f96-4461-b296-39460b58ea78"
}
```

**Tool Result Structure**:
- `tool_use_id`: ID linking back to the tool use
- `content`: String result (backend normalizes arrays to strings)
- `is_error`: Boolean or null (falsy values = success)
- `timestamp`: When result was received

## Message Type: Assistant

### Simple Assistant Response
```json
{
  "type": "assistant",
  "content": "I can help you with that!",
  "timestamp": 1761073198.2721586,
  "metadata": {
    "model": "claude-sonnet-4-5-20250929",
    "has_tool_uses": false,
    "has_thinking": false,
    "has_tool_results": false
  }
}
```

### Assistant with Tool Uses
```json
{
  "type": "assistant",
  "content": "Assistant response",
  "timestamp": 1761073198.2721586,
  "metadata": {
    "tool_uses": [
      {
        "id": "toolu_01SMt2ETHn8ocNGdRXJaWaWF",
        "name": "mcp__legion__send_comm",
        "input": {
          "to_minion_name": "fred",
          "summary": "Q1: Is it an appliance?",
          "content": "**Question 1:** Is it an electrical appliance?\n\n**My guess:** Toaster",
          "comm_type": "question"
        },
        "timestamp": 1761073198.2721586
      }
    ],
    "has_tool_uses": true,
    "has_tool_results": false,
    "has_thinking": false,
    "model": "claude-sonnet-4-5-20250929",
    "session_id": "0801e1da-1f96-4461-b296-39460b58ea78"
  },
  "session_id": "0801e1da-1f96-4461-b296-39460b58ea78"
}
```

**Tool Use Structure**:
- `id`: Unique tool call ID (format: `toolu_...`)
- `name`: Tool name (may include `mcp__` prefix)
- `input`: Object with tool-specific parameters
- `timestamp`: When tool was called

### Assistant with Thinking
```json
{
  "type": "assistant",
  "content": "Let me think about this...",
  "timestamp": 1761073198.2721586,
  "metadata": {
    "thinking_content": "I should consider multiple approaches to solve this problem...",
    "thinking_blocks": [
      {
        "content": "I should consider multiple approaches to solve this problem...",
        "timestamp": 1761073198.2721586
      }
    ],
    "has_thinking": true,
    "has_tool_uses": false,
    "model": "claude-sonnet-4-5-20250929"
  }
}
```

**Thinking Structure**:
- `thinking_content`: Full thinking text (for quick access)
- `thinking_blocks`: Array of thinking segments with timestamps
- Display as collapsible block

## Tool Call Lifecycle

Based on the messages, here's how tool calls flow:

1. **Assistant sends tool use** (`AssistantMessage`):
   ```json
   {
     "type": "assistant",
     "metadata": {
       "tool_uses": [{"id": "toolu_xxx", "name": "Read", "input": {...}}]
     }
   }
   ```

2. **User sends tool result** (`UserMessage`):
   ```json
   {
     "type": "user",
     "metadata": {
       "tool_results": [{"tool_use_id": "toolu_xxx", "content": "...", "is_error": false}]
     }
   }
   ```

3. **Link by ID**: Match `tool_results[].tool_use_id` to `tool_uses[].id`

## Frontend Tracking Strategy

Store tool calls separately from messages:

```javascript
// In message store
const toolCalls = ref(new Map())  // Map<toolUseId, ToolCallState>

interface ToolCallState {
  id: string                    // From tool_uses[].id
  name: string                  // From tool_uses[].name
  status: 'pending' | 'executing' | 'completed' | 'error'
  input: object                 // From tool_uses[].input
  result: string | null         // From tool_results[].content
  isError: boolean             // From tool_results[].is_error
  timestamp: number            // From tool_uses[].timestamp
  resultTimestamp: number | null  // From tool_results[].timestamp
}

// Lifecycle
1. See tool_use → Create with status='pending'
2. See tool_result → Update with result + status='completed'/'error'
```

## Common Tool Names Found

From examining messages:
- `mcp__legion__send_comm` - Legion communication
- `Read` - Read file
- `Edit` - Edit file
- `Write` - Write file
- `Bash` - Execute bash command
- `TodoWrite` - Update todo list
- `Grep` - Search in files
- `Glob` - Find files by pattern
- `WebFetch` - Fetch web content
- `Task` - Launch sub-agent

## Edge Cases to Handle

1. **Missing metadata**: Some messages may not have metadata object
2. **Null values**: `is_error` can be `null` (treat as false)
3. **Empty tool arrays**: `tool_uses: []` or `tool_results: []`
4. **Content variations**: Backend normalizes but be defensive
5. **Orphan tool results**: Result without matching tool use (shouldn't happen but handle gracefully)

## Validation Checklist

When processing messages:
- ✅ Check `type` field exists
- ✅ Provide default empty string for missing `content`
- ✅ Handle both timestamp formats
- ✅ Safely access nested metadata fields
- ✅ Validate tool use/result structures before rendering
- ✅ Handle unknown tool names with fallback component

---

**Last Updated**: Phase 3 implementation
**Data Source**: `test_data/sessions/*/messages.jsonl` files
