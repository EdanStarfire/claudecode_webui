// Pure prepend-merge for tail-first history loading (issue #2030 stage 1b).
//
// The browser holds one contiguous suffix of a session's history. Backfill pages arrive
// newest-to-oldest and are merged UNDER the existing state; this module owns the merge so it is
// testable without Pinia (message.js's applyOlderPage() wraps it with the store side effects).

function isEmptyInput(input) {
  return input === undefined || input === null ||
    (typeof input === 'object' && !Array.isArray(input) && Object.keys(input).length === 0)
}

function isAbsent(value) {
  return value === undefined || value === null
}

/**
 * Fills `current` (the newer, already-installed tool call) from `older` field by field. The
 * newer entry wins whenever it carries a value; the older record only fills what is absent.
 * Status never regresses and the `_isOrphaned` / `permissionDecision` locks are preserved
 * (they are only ever set on the newer entry). Mutates and returns `current`, so entry identity
 * (and any UI state hanging off it, e.g. `isExpanded`) survives the merge.
 *
 * Why not just replay the older record through the live update path: `_applyToolCallRecord`
 * always accepts an incoming `input`, so an older page applied after a newer one would overwrite
 * a completed tool's full `input` with the `{}` of its first, pre-streaming record.
 */
export function mergeToolCallFields(current, older) {
  if (!current.name && older.name) current.name = older.name
  if (isEmptyInput(current.input) && !isEmptyInput(older.input)) current.input = older.input
  if (isAbsent(current.timestamp) && !isAbsent(older.timestamp)) current.timestamp = older.timestamp
  if (isAbsent(current.messageId) && !isAbsent(older.messageId)) current.messageId = older.messageId
  if (isAbsent(current.parent_tool_use_id) && !isAbsent(older.parent_tool_use_id)) {
    current.parent_tool_use_id = older.parent_tool_use_id
  }
  if (isAbsent(current.permissionRequestId) && !isAbsent(older.permissionRequestId)) {
    current.permissionRequestId = older.permissionRequestId
  }
  if (isAbsent(current.permissionDecision) && !isAbsent(older.permissionDecision)) {
    current.permissionDecision = older.permissionDecision
  }
  if ((!current.suggestions || current.suggestions.length === 0) && older.suggestions?.length) {
    current.suggestions = older.suggestions
  }
  if ((!current.appliedUpdates || current.appliedUpdates.length === 0) && older.appliedUpdates?.length) {
    current.appliedUpdates = older.appliedUpdates
  }
  if (isAbsent(current.result) && !isAbsent(older.result)) current.result = older.result
  for (const field of ['autoApprovedReason', 'agentId', 'senderAttachments', 'answers']) {
    if (isAbsent(current[field]) && !isAbsent(older[field])) current[field] = older[field]
  }
  return current
}

/**
 * Merges an older, contiguous page's accumulator UNDER the current state.
 *
 * `current` / `older` are record-target shaped ({ messages, toolCalls, retryIndex, ... });
 * `current.messageIndex` / `current.toolIndex` are consulted for duplicate detection. Returns a
 * fresh { messages, toolCalls, messageIndex, toolIndex, retryIndex } — the inputs' arrays are
 * not reused, but tool-call entries that exist in both are the CURRENT objects (field-filled).
 *
 * Order matches a full load: messages = older ++ current (duplicates by message_id dropped);
 * tool calls are ordered by first appearance, so a tool whose first record sits in the older
 * page takes its older position while keeping the current entry object.
 */
export function mergeOlderPage(current, older) {
  const olderMessages = older.messages.filter(
    (m) => !(m.message_id && current.messageIndex.has(m.message_id))
  )
  const messages = olderMessages.length ? olderMessages.concat(current.messages) : current.messages.slice()

  const messageIndex = new Map()
  messages.forEach((m, i) => {
    if (m.message_id) messageIndex.set(m.message_id, i)
  })
  // retry_message_id entries only exist for live-sourced api_retry records; they point at
  // current positions, which shift right by the number of prepended messages. Entries are
  // re-pointed through the message_id of the slot they referenced when that is available.
  const retryIndex = new Map()
  for (const [retryId, idx] of current.retryIndex) retryIndex.set(retryId, idx + olderMessages.length)

  const currentToolIds = current.toolIndex
  const toolCalls = []
  const seen = new Set()
  for (const olderTool of older.toolCalls) {
    const idx = currentToolIds.get(olderTool.id)
    if (idx !== undefined) {
      toolCalls.push(mergeToolCallFields(current.toolCalls[idx], olderTool))
      seen.add(idx)
    } else {
      toolCalls.push(olderTool)
    }
  }
  current.toolCalls.forEach((tool, idx) => {
    if (!seen.has(idx)) toolCalls.push(tool)
  })
  const toolIndex = new Map()
  toolCalls.forEach((t, i) => toolIndex.set(t.id, i))

  return { messages, toolCalls, messageIndex, toolIndex, retryIndex, prependedMessageCount: olderMessages.length }
}
