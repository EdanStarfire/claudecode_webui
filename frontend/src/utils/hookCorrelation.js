/**
 * hookCorrelation.js
 *
 * Pure function to correlate hook system messages to their parent UI elements.
 * No Vue/Pinia coupling — can be unit-tested directly.
 *
 * Hook messages (hook_started / hook_response) do not carry tool_use_id.
 * Correlation is performed by forward pass over the message stream using
 * stream position and hook_event type.
 *
 * @see plan-issue-1350.md §2
 */

/**
 * @typedef {Object} HookIndicator
 * @property {string|null} hook_id
 * @property {string|null} hook_name
 * @property {string|null} hook_event
 * @property {'pending'|'success'|'failure'} status
 * @property {number|null} exit_code
 * @property {string|null} stdout
 * @property {string|null} stderr
 * @property {string|null} started_message_id
 * @property {string|null} response_message_id
 * @property {number|null} started_ts
 * @property {number|null} completed_ts
 */

/**
 * @typedef {Object} HookCorrelationResult
 * @property {Map<string, HookIndicator[]>} hooksByToolId        tool_use_id → hooks
 * @property {Map<string, HookIndicator[]>} hooksByMessageId     user/assistant message_id → hooks
 * @property {Map<number, HookIndicator[]>} hooksByCompactionIndex compaction group ordinal → hooks
 * @property {HookIndicator[]} hooksOnClientLaunched
 * @property {Set<string>} attachedHookMessageIds  hook message IDs successfully correlated (hide from pill)
 * @property {Set<string>} unattachedHookMessageIds hook message IDs falling back to standalone pill
 */

function getMsgId(msg) {
  return msg.message_id || msg.id || null
}

function addToMap(map, key, indicator) {
  if (!map.has(key)) map.set(key, [])
  map.get(key).push(indicator)
}

/**
 * Correlate hook messages to their parent UI elements.
 *
 * @param {Array} messages  All messages from messagesBySession (raw, unfiltered)
 * @param {Array} [toolCalls]  Tool calls from toolCallsBySession (unused in core logic, reserved for future)
 * @returns {HookCorrelationResult}
 */
export function correlateHooks(messages, toolCalls = []) {
  const hooksByToolId = new Map()
  const hooksByMessageId = new Map()
  const hooksByCompactionIndex = new Map()
  const hooksOnClientLaunched = []
  const attachedHookMessageIds = new Set()
  const unattachedHookMessageIds = new Set()

  // Pending HookIndicators awaiting hook_response, keyed by hook_id
  const pendingByHookId = new Map()

  // Rolling anchors
  let lastUserMessageId = null
  let lastAssistantMessageId = null
  let lastClientLaunchedId = null
  let currentCompactionGroupIndex = -1  // incremented when status=compacting seen
  let lastCompletedToolId = null

  // FIFO queue of tool_use_ids seen in assistant messages but whose tool_result has not yet arrived.
  // PreToolUse hooks peek at [0] (oldest pending). tool_results remove from this queue.
  // Risk #5: flat ordered queue handles multiple same-name tools correctly.
  const pendingPreToolQueue = []

  for (const msg of messages) {
    const subtype = msg.metadata?.subtype

    // ── User message ──────────────────────────────────────────────────────────
    if (msg.type === 'user') {
      const hasToolResults = msg.metadata?.has_tool_results
      const content = msg.content || ''
      const isPureToolResult = hasToolResults &&
        (content.match(/^Tool results?: \d+ results?$/i) || content.trim() === '')

      if (!isPureToolResult) {
        lastUserMessageId = getMsgId(msg)
      }

      if (msg.metadata?.tool_results) {
        for (const result of msg.metadata.tool_results) {
          lastCompletedToolId = result.tool_use_id
          const idx = pendingPreToolQueue.indexOf(result.tool_use_id)
          if (idx !== -1) pendingPreToolQueue.splice(idx, 1)
        }
      }
    }

    // ── Assistant message ─────────────────────────────────────────────────────
    if (msg.type === 'assistant') {
      lastAssistantMessageId = getMsgId(msg)

      if (msg.metadata?.tool_uses) {
        for (const toolUse of msg.metadata.tool_uses) {
          if (toolUse.id && !pendingPreToolQueue.includes(toolUse.id)) {
            pendingPreToolQueue.push(toolUse.id)
          }
        }
      }
    }

    // ── System messages ───────────────────────────────────────────────────────
    if (msg.type === 'system') {
      if (subtype === 'client_launched') {
        lastClientLaunchedId = getMsgId(msg)
      }

      // Track compaction group ordinal (each compacting status = new group)
      if (subtype === 'status' && msg.metadata?.init_data?.status === 'compacting') {
        currentCompactionGroupIndex++
      }

      if (subtype === 'hook_started' || subtype === 'hook_response') {
        const msgId = getMsgId(msg)
        const initData = msg.metadata?.init_data || {}
        const hookId = initData.hook_id
        const hookEvent = initData.hook_event
        const hookName = initData.hook_name

        if (subtype === 'hook_started') {
          const baseIndicator = {
            hook_id: hookId || null,
            hook_name: hookName || null,
            hook_event: hookEvent || null,
            status: 'pending',
            exit_code: null,
            stdout: null,
            stderr: null,
            started_message_id: msgId,
            response_message_id: null,
            started_ts: msg.timestamp || null,
            completed_ts: null,
          }

          let indicator = null
          let attached = false

          switch (hookEvent) {
            case 'PreToolUse':
              if (pendingPreToolQueue.length > 0) {
                // Peek at oldest pending tool (FIFO, don't dequeue yet — tool_result does that)
                const toolId = pendingPreToolQueue[0]
                indicator = { ...baseIndicator }
                addToMap(hooksByToolId, toolId, indicator)
                attached = true
              }
              break

            case 'PostToolUse':
              if (lastCompletedToolId) {
                indicator = { ...baseIndicator }
                addToMap(hooksByToolId, lastCompletedToolId, indicator)
                attached = true
              }
              break

            case 'UserPromptSubmit':
              if (lastUserMessageId) {
                indicator = { ...baseIndicator }
                addToMap(hooksByMessageId, lastUserMessageId, indicator)
                attached = true
              }
              break

            case 'Stop':
            case 'SubagentStop':
            case 'PostResponse':
              if (lastAssistantMessageId) {
                indicator = { ...baseIndicator }
                addToMap(hooksByMessageId, lastAssistantMessageId, indicator)
                attached = true
              }
              break

            case 'PreCompact':
              if (currentCompactionGroupIndex >= 0) {
                indicator = { ...baseIndicator }
                addToMap(hooksByCompactionIndex, currentCompactionGroupIndex, indicator)
                attached = true
              }
              break

            default:
              // SessionStart and unknown events: attach to client_launched if present
              if (lastClientLaunchedId) {
                indicator = { ...baseIndicator }
                hooksOnClientLaunched.push(indicator)
                attached = true
              }
              break
          }

          if (attached && msgId) {
            attachedHookMessageIds.add(msgId)
            if (indicator && hookId) {
              pendingByHookId.set(hookId, indicator)
            }
          } else if (msgId) {
            unattachedHookMessageIds.add(msgId)
          }
        }

        if (subtype === 'hook_response') {
          const exitCode = initData.exit_code ?? initData.exitCode ?? null
          const stdout = initData.stdout || null
          const stderr = initData.stderr || null
          const outcome = initData.outcome || null

          const pendingIndicator = hookId ? pendingByHookId.get(hookId) : null

          if (pendingIndicator) {
            if (exitCode !== null && exitCode !== undefined) {
              pendingIndicator.status = exitCode === 0 ? 'success' : 'failure'
            } else if (outcome === 'approved') {
              pendingIndicator.status = 'success'
            } else if (outcome === 'failed') {
              pendingIndicator.status = 'failure'
            } else {
              pendingIndicator.status = 'success'
            }
            pendingIndicator.exit_code = exitCode
            pendingIndicator.stdout = stdout
            pendingIndicator.stderr = stderr
            pendingIndicator.response_message_id = msgId
            pendingIndicator.completed_ts = msg.timestamp || null
            pendingByHookId.delete(hookId)

            if (msgId) attachedHookMessageIds.add(msgId)
          } else {
            // No matching hook_started found — fall back to pill
            if (msgId) unattachedHookMessageIds.add(msgId)
          }
        }
      }
    }
  }

  return {
    hooksByToolId,
    hooksByMessageId,
    hooksByCompactionIndex,
    hooksOnClientLaunched,
    attachedHookMessageIds,
    unattachedHookMessageIds,
  }
}

/**
 * Compute aggregate status for a list of HookIndicators.
 * failure dominates, then pending, then success.
 *
 * @param {HookIndicator[]} hooks
 * @returns {'success'|'failure'|'pending'|'mixed'|null}
 */
export function aggregateHookStatus(hooks) {
  if (!hooks || hooks.length === 0) return null
  let hasFailure = false
  let hasPending = false
  let hasSuccess = false
  for (const h of hooks) {
    if (h.status === 'failure') hasFailure = true
    else if (h.status === 'pending') hasPending = true
    else if (h.status === 'success') hasSuccess = true
  }
  if (hasFailure && (hasPending || hasSuccess)) return 'mixed'
  if (hasFailure) return 'failure'
  if (hasPending) return 'pending'
  return 'success'
}
