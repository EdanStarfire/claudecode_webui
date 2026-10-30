// Pure displayable-item pipeline for MessageList.vue (issue #2030 stage 1b).
//
// Extracted verbatim from MessageList.vue so it can be exercised without mounting the component
// (prepend-stability tests, the AC8 equivalence harness) — behavior is pinned by
// MessageList.test.js. Everything the pipeline used to read off the Pinia stores is passed in
// through `ctx` instead. The caller builds `ctx` INSIDE its computed so reactive reads
// (tool calls, task-leg entries, hook correlation) stay tracked:
//
//   ctx = {
//     sessionId,
//     messages,                        // the session's message array
//     toolCalls,                       // the session's tool-call array
//     legEntries,                      // messageStore.allTaskLegEntriesForSession(sessionId)
//     getTaskIdForLaunchToolUse(id),   // messageStore.getTaskIdForLaunchToolUse
//     isHookMessageAttached(messageId),
//     openBefore,                      // the interval has older history that is not loaded
//     openAfter,                       // the interval has newer history that is not loaded (an island)
//   }
// (Issue #2030 stage 3 PR 3C-1: the pipeline runs once PER loaded interval; buildIntervalItems()
// below joins the per-interval rows with a `gap` row. Leg signals are scoped to the interval's own
// time span instead of depending on a session-wide "history complete" flag.)
import { parseTimestamp, formatDateSeparatorLabel } from '@/utils/time'
import { getEffectiveStatusForTool } from '@/composables/useToolStatus'
import { getAgentColor, slugifyAgentName } from '@/composables/useAgentColor'
import { resolveMessageId } from '@/search/messageSearch'

/**
 * Group messages into displayable items, detecting compaction event sequences
 * and attaching tools to their parent assistant messages
 * Returns array of:
 * - Regular messages: { type: 'message', message: {...}, attachedTools: [...] }
 * - Compaction events: { type: 'compaction', messages: [{...}, {...}, {...}, {...}] }
 */
export function buildDisplayableItems(ctx) {
  const messages = ctx.messages
  const items = []
  let i = 0

  while (i < messages.length) {
    const msg = messages[i]

    // Check for compaction event FIRST (before shouldDisplayMessage filter)
    // This is critical because the status messages may be filtered by shouldDisplayMessage
    if (isCompactionStart(msg, messages, i)) {
      // Determine if this is a 4-message or 5-message pattern
      const msg3 = messages[i + 2]
      const hasInitMessage = msg3?.type === 'system' && msg3?.metadata?.subtype === 'init'
      const messageCount = hasInitMessage ? 5 : 4

      const compactionMessages = messages.slice(i, i + messageCount)
      items.push({
        type: 'compaction',
        messages: compactionMessages,
        // Issue #2030: the first record's message_id keys the group (PreCompact hook lookup)
        groupKey: resolveMessageId(compactionMessages[0]),
      })
      i += messageCount // Skip the compaction messages
      continue
    }

    // Regular message filtering
    if (shouldDisplayMessage(msg, ctx)) {
      items.push({
        type: 'message',
        message: msg,
        attachedTools: [] // Will be populated by tool grouping
      })
    }

    i++
  }

  // Second pass: Group tools to parent assistant messages
  // Third pass: Inject date separators between items on different calendar dates
  // Fourth pass: Inject subagent-signal anchors (pushed-to-main, leg-terminal) at their own
  //   chronological position — BEFORE merging, so a signal correctly breaks a would-be merge
  //   between two assistant turns it causally sits between (Issue #1746 follow-up)
  // Fifth pass: Merge consecutive assistant turns into one visual block (Issue #1746)
  // Sixth pass: Attach any permission_required tools not yet anchored to a bubble
  // Seventh pass: mark purely-visual message_id continuations (Issue #1957 follow-up to #1955)
  return markMessageIdContinuations(
    attachOrphanedPermissionTools(
      mergeConsecutiveAssistantTurns(
        injectSubagentSignals(injectDateSeparators(groupToolsToParentMessages(items)), ctx)
      ),
      ctx,
    )
  )
}

/**
 * Rows of the whole loaded history: the pipeline per interval, a `gap` row between an island and
 * the suffix. `ctx` is the usual pipeline ctx WITHOUT messages/toolCalls; `intervals` is
 * `[{ key, messages, toolCalls, hasMoreBefore, hasMoreAfter }]` oldest first, `gap` the stretch
 * between the first two (or null). With a single interval this is exactly buildDisplayableItems().
 */
export function buildIntervalItems(ctx) {
  const rows = []
  ctx.intervals.forEach((interval, i) => {
    const part = buildDisplayableItems({
      ...ctx,
      messages: interval.messages,
      toolCalls: interval.toolCalls,
      openBefore: !!interval.hasMoreBefore,
      openAfter: !!interval.hasMoreAfter,
    })
    for (const item of part) rows.push(item)
    if (i < ctx.intervals.length - 1 && ctx.gap) {
      rows.push({ type: 'gap', ...ctx.gap, key: `gap:${ctx.gap.fromCursor}` })
    }
  })
  return rows
}

function timestampForItem(item) {
  if (item.type === 'compaction') return item.messages[0]?.timestamp
  return item.message?.timestamp
}

function localDateKey(timestamp) {
  return parseTimestamp(timestamp).toDateString()
}

/**
 * Issue #1746 (stage: subagents) follow-up / spec §4.2: subagent events that "interact with
 * the main session" — a message pushed to main, or a leg ending (completed/failed/stopped) —
 * must render inline in the MAIN timeline itself, not only inside the subagent's own nested
 * card, or the main assistant's reaction to one reads as unprompted (no visible causality).
 *
 * Collects these as lightweight anchor items and inserts each one at the position of the first
 * existing item whose own timestamp is at or after the signal's — i.e. immediately before
 * whatever main-thread item first reacts to it, chronologically. Runs BEFORE
 * mergeConsecutiveAssistantTurns() so a signal correctly interrupts a would-be merge between
 * two assistant turns it causally sits between.
 */
function collectSubagentSignals(ctx) {
  if (!ctx.sessionId) return []
  const signals = []

  // "Pushed to main": a subagent delivering a message to main. Not a leg boundary — the leg
  // keeps running. Scoped to the SendMessage tool for now (the shape confirmed by real repro
  // data); mcp__legion__send_comm targeting has different semantics and isn't wired here.
  for (const tc of ctx.toolCalls) {
    if (tc.name !== 'SendMessage') continue
    if (!tc.parent_tool_use_id) continue // only subagent-originated (main's own calls stay in ActivityTimeline)
    if (tc.input?.to !== 'main') continue
    if (tc.status !== 'completed') continue // only once delivery is confirmed
    const taskId = ctx.getTaskIdForLaunchToolUse(tc.parent_tool_use_id)
    if (!taskId) continue
    const ts = tc.timestamp
    if (ts == null) continue
    signals.push({
      type: 'subagent_signal',
      anchorType: 'pushed',
      taskId,
      agentColor: getAgentColor(slugifyAgentName(taskId)),
      // Issue #1746 follow-up (user feedback): this content guides the main session — it must
      // render with full markdown support, not a single truncated line. Header keeps a short
      // static label; the actual message goes in markdownBody (SubagentAnchorRow.vue).
      description: 'Message to main',
      markdownBody: tc.input?.message || tc.input?.content || tc.input?.summary || 'Message sent to main',
      statusText: '',
      timestamp: ts,
      _sortTs: parseTimestamp(ts).getTime(),
      key: `subagent-signal-pushed-${tc.id}`,
    })
  }

  // Leg-terminal: each leg that reached a terminal status (completed/failed/stopped). Uses
  // THAT leg's own description (set at its own task_started) — entry-level plain objects here
  // don't carry a TaskLegEntry-style "latest leg" description getter the way the backend
  // dataclass does, and the per-leg value is more precise anyway.
  for (const entry of ctx.legEntries) {
    entry.legs.forEach((leg, legIndex) => {
      if (leg.status === 'running' || leg.ended_at == null) return
      signals.push({
        type: 'subagent_signal',
        anchorType: 'completed',
        taskId: entry.task_id,
        agentColor: getAgentColor(slugifyAgentName(entry.task_id)),
        description: leg.description || 'Agent',
        // Issue #1746 follow-up (user feedback): the subagent's own final report (leg.result,
        // from task_notification's summary) must be shown, not just a bare status badge —
        // with the same full markdown support as the header's pushed-to-main content.
        markdownBody: leg.result || null,
        statusText: leg.status,
        timestamp: leg.ended_at,
        _sortTs: parseTimestamp(leg.ended_at).getTime(),
        key: `subagent-signal-terminal-${entry.task_id}-${legIndex}`,
        // Issue #1746 follow-up: SubagentGlobalGutter.vue measures a leg's END position from
        // this id — this row IS that leg's terminal anchor now (SubagentTimeline.vue no longer
        // renders one glued to the launch position; doing so there made every historical lane's
        // end sit right next to its start regardless of how much happened in between).
        domId: `subagent-anchor-terminal-${entry.task_id}-${legIndex}`,
      })
    })
  }

  return signals
}

function injectSubagentSignals(items, ctx) {
  let signals = collectSubagentSignals(ctx)
  if (signals.length === 0) return items

  const itemTimestamps = items.map(item => {
    const ts = timestampForItem(item)
    return ts ? parseTimestamp(ts).getTime() : null
  })

  // Issue #2030: leg-terminal signals are built from the FULL background-agents snapshot, but
  // are placed at "the first item at or after the signal's timestamp" — for a leg that ended
  // before the interval began that is index 0, so every such signal would stack at the top of a
  // partially loaded list (and one that ended after an island's last item at its bottom). Signals
  // are therefore scoped to the interval's own time span: those preceding its first item are
  // dropped while it has older history not loaded (`openBefore`), those following its last item
  // while it has newer history not loaded (`openAfter`); they reappear once the neighbouring
  // history is loaded into the same interval.
  if (ctx.openBefore || ctx.openAfter) {
    const loaded = itemTimestamps.filter(ts => ts != null)
    if (loaded.length) {
      const first = loaded[0]
      const last = loaded[loaded.length - 1]
      signals = signals.filter(signal =>
        (!ctx.openBefore || signal._sortTs >= first) && (!ctx.openAfter || signal._sortTs <= last))
    }
    if (signals.length === 0) return items
  }

  // Insert in ascending timestamp order so multiple signals landing in the same gap keep their
  // own relative order instead of each independently re-scanning from index 0.
  signals.sort((a, b) => a._sortTs - b._sortTs)
  for (const signal of signals) {
    let insertAt = items.length
    for (let i = 0; i < items.length; i++) {
      if (itemTimestamps[i] != null && itemTimestamps[i] >= signal._sortTs) { insertAt = i; break }
    }
    items.splice(insertAt, 0, signal)
    itemTimestamps.splice(insertAt, 0, signal._sortTs)
  }
  return items
}

function injectDateSeparators(items) {
  if (items.length === 0) return items
  const out = []
  let prevKey = null
  for (const item of items) {
    const ts = timestampForItem(item)
    const key = ts ? localDateKey(ts) : null
    if (key !== null && key !== prevKey) {
      out.push({ type: 'date_separator', label: formatDateSeparatorLabel(ts), dateKey: key })
    }
    out.push(item)
    if (key !== null) prevKey = key
  }
  return out
}

/**
 * Group tools from content-less assistant messages to the most recent
 * assistant message with content, BUT only if no non-assistant message
 * is encountered during the search.
 *
 * Algorithm:
 * - First pass (forward): Build attachment map
 * - Second pass (forward): Apply attachments and filter
 *
 * This ensures tools appear in chronological order after triggering messages,
 * and consecutive empty assistants consolidate into one message row.
 */
function groupToolsToParentMessages(items) {
  // First pass: Determine where each item's tools should attach
  const attachMap = new Map() // index -> array of tool_uses to attach
  const hideSet = new Set() // indices to hide

  for (let i = 0; i < items.length; i++) {
    const item = items[i]

    // Skip compaction events
    if (item.type === 'compaction') {
      continue
    }

    const msg = item.message

    // Check if this is an assistant message with tools but no content
    const hasContent = msg.content && msg.content.trim().length > 0 && msg.content !== 'Assistant response'
    const hasTools = msg.type === 'assistant' && msg.metadata?.has_tool_uses && msg.metadata?.tool_uses?.length > 0

    if (msg.type === 'assistant' && hasTools && !hasContent) {
      // This is a content-less assistant message with tools
      // Search backwards for assistant with content OR first empty assistant that's NOT hidden
      let parentIndex = -1
      let hitNonAssistant = false

      for (let j = i - 1; j >= 0; j--) {
        const candidateItem = items[j]

        // Skip items already marked for hiding
        if (hideSet.has(j)) {
          continue
        }

        // If we hit a non-assistant message (user/system), stop searching
        if (candidateItem.type === 'message' && candidateItem.message.type !== 'assistant') {
          hitNonAssistant = true
          break
        }

        // Check if this is an assistant message
        if (candidateItem.type === 'message' && candidateItem.message.type === 'assistant') {
          const candidateMsg = candidateItem.message
          const candidateHasContent = candidateMsg.content && candidateMsg.content.trim().length > 0 && candidateMsg.content !== 'Assistant response'

          if (candidateHasContent) {
            // Found assistant with content - use it
            parentIndex = j
            break
          } else {
            // Found empty assistant - use it as consolidation point
            parentIndex = j
            break
          }
        }
      }

      if (hitNonAssistant && parentIndex === -1) {
        // Hit non-assistant but found no assistant after it
        // Keep this empty assistant message - it's the first after the blocker
        // Tools stay on this message
      } else if (hitNonAssistant && parentIndex >= 0) {
        // Hit non-assistant and found empty assistant after it
        // Consolidate tools into that first empty assistant
        if (!attachMap.has(parentIndex)) {
          attachMap.set(parentIndex, [])
        }
        attachMap.get(parentIndex).push(...msg.metadata.tool_uses)
        hideSet.add(i) // Hide this message
      } else if (!hitNonAssistant && parentIndex >= 0) {
        // No blocker, found parent assistant (with or without content)
        // Attach tools to parent, hide this empty message
        if (!attachMap.has(parentIndex)) {
          attachMap.set(parentIndex, [])
        }
        attachMap.get(parentIndex).push(...msg.metadata.tool_uses)
        hideSet.add(i) // Hide this message
      }
      // else: No parent found at all - keep this message with its tools
    }
  }

  // Second pass: Apply attachments and build result
  const processedItems = []

  for (let i = 0; i < items.length; i++) {
    // Skip items marked for hiding
    if (hideSet.has(i)) {
      continue
    }

    const item = items[i]

    // Apply attachments if any
    if (attachMap.has(i)) {
      item.attachedTools = [...(item.attachedTools || []), ...attachMap.get(i)]
    }

    processedItems.push(item)
  }

  return processedItems
}

/**
 * Issue #1746 (stage: layout): merge consecutive assistant turns into one visual block.
 *
 * A run starts at any 'assistant' message item and extends onto the next item only if:
 * - the next item is also an 'assistant' message item, AND
 * - the current run's last member is not itself a merge boundary (see isMergeBoundary below).
 *
 * A user item, subagent-related item, date separator, or compaction group always closes the
 * run, since none of those satisfy isAssistantMessageItem().
 *
 * Runs of length 1 pass through unchanged. Runs of length >= 2 collapse into the first item,
 * with the rest attached as `mergedMessages` (each a shallow copy of its message plus its own
 * attachedTools) and dropped from the top-level list — mirroring how groupToolsToParentMessages()
 * already hides consolidated items above.
 */
function isMergeBoundary(item) {
  // Task/Agent/send_comm calls render SubagentTimeline/SendCommToolHandler as siblings AFTER
  // the bubble — a message carrying one of these may be the LAST member of a run, but nothing
  // may visually merge past it (matches AssistantMessage.vue's last-segment-only sourcing).
  // Must check attachedTools too: groupToolsToParentMessages() (the pass just before this one)
  // routinely moves a Task/Agent/send_comm tool_use from a content-less trailing message onto
  // this item's attachedTools rather than leaving it in item.message.metadata.tool_uses —
  // missing that here would silently drop the tool call from rendering (AssistantMessage.vue
  // only sources taskToolCalls/sendCommToolCalls from a run's LAST segment).
  const tools = [
    ...(item.message?.metadata?.tool_uses || []),
    ...(item.attachedTools || []),
  ]
  return tools.some(t => t.name === 'Task' || t.name === 'Agent' || t.name === 'mcp__legion__send_comm')
}

function isAssistantMessageItem(item) {
  return item.type === 'message' && item.message?.type === 'assistant'
}

function mergeConsecutiveAssistantTurns(items) {
  const result = []
  let i = 0

  while (i < items.length) {
    const item = items[i]

    if (!isAssistantMessageItem(item)) {
      result.push(item)
      i++
      continue
    }

    const run = [item]
    let j = i + 1
    while (
      !isMergeBoundary(run[run.length - 1]) &&
      j < items.length &&
      isAssistantMessageItem(items[j])
    ) {
      run.push(items[j])
      j++
    }

    if (run.length === 1) {
      result.push(item)
    } else {
      const [head, ...rest] = run
      head.mergedMessages = rest.map(it => ({
        ...it.message,
        attachedTools: it.attachedTools || [],
      }))
      result.push(head)
    }

    i = j
  }

  return result
}

/**
 * Issue #1957 (visual grouping, follow-up to #1955); renamed for #1958: purely presentational
 * grouping for multiple canonical AssistantMessage frames sharing the same metadata.turn_id —
 * the #1765 background-Task-launch case, where one Anthropic turn arrives as several separate
 * backend-persisted frames (each with its own distinct, real top-level message_id — verified
 * against backend/data_storage.py — but a shared metadata.turn_id, the Anthropic streaming
 * id). Unlike mergeConsecutiveAssistantTurns() above, this does NOT touch the item list: every
 * frame keeps its own independent virtualizer row, its own identity, its own height
 * measurement. It only stamps two booleans consumed purely for CSS by AssistantMessage.vue —
 * `isMessageIdContinuation` (render this item's own first segment like a continuation: no
 * role/timestamp header, no top spacing) and `hasMessageIdContinuationFollowing` (no bottom
 * spacing, since the next row butts directly against it) — so two adjacent rows read as one
 * seamless card with no code path anywhere touching what the virtualizer thinks the row list
 * contains.
 *
 * Adjacency deliberately SKIPS OVER purely structural entries injected earlier in this same
 * pipeline (subagent_signal, date_separator, compaction) — a background leg's own
 * leg-terminal signal commonly lands chronologically between two frames of the SAME turn,
 * which is the whole reason this pass exists (mergeConsecutiveAssistantTurns' own strict
 * adjacency check already handles the case with nothing in between). It does NOT skip past a
 * genuine user or unrelated-assistant message — those are real conversation turns, not
 * decoration, and their presence means the frames are no longer visually adjacent.
 */
function markMessageIdContinuations(items) {
  function tailMessageOf(item) {
    if (item.mergedMessages && item.mergedMessages.length > 0) {
      return item.mergedMessages[item.mergedMessages.length - 1]
    }
    return item.message
  }

  let prevMessageItem = null
  for (const item of items) {
    if (item.type !== 'message') continue // skip subagent_signal/date_separator/compaction

    const msg = item.message
    const prevTail = prevMessageItem ? tailMessageOf(prevMessageItem) : null
    if (
      msg.type === 'assistant' &&
      prevTail?.type === 'assistant' &&
      msg.metadata?.turn_id &&
      prevTail.metadata?.turn_id === msg.metadata.turn_id
    ) {
      item.isMessageIdContinuation = true
      prevMessageItem.hasMessageIdContinuationFollowing = true
    }

    prevMessageItem = item
  }

  return items
}

/**
 * Check if message at index i is the start of a compaction event
 * Pattern (with optional init message):
 * 1. System (subtype=status) - status = 'compacting'
 * 2. System (subtype=status) - status = null
 * 3. [OPTIONAL] System (subtype=init) - new session init after compaction
 * 4. System (subtype=compact_boundary)
 * 5. User (starts with "This session is being continued...")
 */
function isCompactionStart(msg, messages, index) {
  // Need at least 4 messages remaining (5 if init is present)
  if (index + 3 >= messages.length) return false

  const msg1 = messages[index]
  const msg2 = messages[index + 1]
  let msg3 = messages[index + 2]
  let msg4 = messages[index + 3]
  let msg5 = messages[index + 4]

  // Message 1: System status=compacting
  const isMsg1Valid =
    msg1.type === 'system' &&
    msg1.metadata?.subtype === 'status' &&
    msg1.metadata?.init_data?.status === 'compacting'

  // Message 2: System status=null
  const isMsg2Valid =
    msg2.type === 'system' &&
    msg2.metadata?.subtype === 'status' &&
    (msg2.metadata?.init_data?.status === null || msg2.metadata?.init_data?.status === undefined)

  // Check if msg3 is an optional init message
  const hasInitMessage = msg3?.type === 'system' && msg3?.metadata?.subtype === 'init'

  // If init message present, shift the expected positions
  if (hasInitMessage) {
    // Pattern becomes: compacting, null, init, compact_boundary, continuation
    if (index + 4 >= messages.length) return false

    const isMsg3Init = true // We already checked this
    const isMsg4Boundary =
      msg4?.type === 'system' &&
      msg4?.metadata?.subtype === 'compact_boundary'
    const isMsg5Continuation =
      msg5?.type === 'user' &&
      msg5?.content?.startsWith('This session is being continued from a previous conversation')

    // Debug logging
    if (msg1.type === 'system' && msg1.metadata?.subtype === 'status') {
      console.log('[Compaction Debug] Checking 5-message pattern (with init) at index', index)
      console.log('  Msg1 (compacting):', isMsg1Valid)
      console.log('  Msg2 (null status):', isMsg2Valid)
      console.log('  Msg3 (init):', isMsg3Init)
      console.log('  Msg4 (boundary):', isMsg4Boundary)
      console.log('  Msg5 (continuation):', isMsg5Continuation)
    }

    return isMsg1Valid && isMsg2Valid && isMsg3Init && isMsg4Boundary && isMsg5Continuation
  } else {
    // Standard 4-message pattern: compacting, null, compact_boundary, continuation
    const isMsg3Boundary =
      msg3?.type === 'system' &&
      msg3?.metadata?.subtype === 'compact_boundary'
    const isMsg4Continuation =
      msg4?.type === 'user' &&
      msg4?.content?.startsWith('This session is being continued from a previous conversation')

    // Debug logging
    if (msg1.type === 'system' && msg1.metadata?.subtype === 'status') {
      console.log('[Compaction Debug] Checking 4-message pattern (no init) at index', index)
      console.log('  Msg1 (compacting):', isMsg1Valid)
      console.log('  Msg2 (null status):', isMsg2Valid)
      console.log('  Msg3 (boundary):', isMsg3Boundary)
      console.log('  Msg4 (continuation):', isMsg4Continuation)
    }

    return isMsg1Valid && isMsg2Valid && isMsg3Boundary && isMsg4Continuation
  }
}

/**
 * Issue #1626 Fix B: Attach any permission_required tools from toolCallsBySession that are
 * not yet referenced by any displayed bubble. This ensures the permission prompt renders
 * even when the bubble's metadata.tool_uses is empty due to the streaming dedup race
 * (before Fix A fully resolves the issue across all SDK variants).
 *
 * Issue #1694: Anchor each orphan to the assistant bubble that produced it by matching
 * tc.messageId against the owning message's TURN-level identity, searching the whole
 * displayed list — not just the last bubble. This is correct regardless of realtime
 * emission timing because it matches on identity, not recency/position. Falls back to
 * the pre-#1694 last-assistant-bubble heuristic only when messageId is absent (legacy
 * stored data) or unresolved (owning bubble not currently displayed/paginated in).
 *
 * Issue #1957/#1958: tc.messageId is `ToolCall.turn_id`, sourced from metadata.turn_id
 * (the Anthropic turn-level id — see backend/web_server.py's create_tool_call() call).
 * Matching it against the top-level `message.message_id`/`seg.message_id` — the PER-RECORD
 * identity #1955's frontend dedup needs — silently never matches for any live-delivered
 * message, since the two identities are deliberately different values. Match against
 * `metadata?.turn_id` instead, which stays turn-level on both sides.
 */
function attachOrphanedPermissionTools(items, ctx) {
  if (!ctx.sessionId) return items
  // An island (newer history not loaded) holds history only: open permission prompts are live
  // state of the suffix and are anchored there.
  if (ctx.openAfter) return items
  const liveTools = ctx.toolCalls
  if (liveTools.length === 0) return items

  // Collect tool_use_ids already referenced by any displayed bubble — including tool_uses
  // that live on a merged-away continuation segment (Issue #1746: mergeConsecutiveAssistantTurns
  // stamps those onto item.mergedMessages rather than leaving them as top-level items).
  const referenced = new Set()
  for (const item of items) {
    if (item.type !== 'message') continue
    const msg = item.message
    if (msg.type === 'assistant') {
      for (const t of msg.metadata?.tool_uses || []) referenced.add(t.id)
      for (const t of item.attachedTools || []) referenced.add(t.id)
    }
    for (const seg of item.mergedMessages || []) {
      for (const t of seg.metadata?.tool_uses || []) referenced.add(t.id)
      for (const t of seg.attachedTools || []) referenced.add(t.id)
    }
  }

  // Find permission_required tools not yet referenced.
  const orphans = []
  for (const tc of liveTools) {
    if (referenced.has(tc.id)) continue
    if (getEffectiveStatusForTool(tc) !== 'permission_required') continue
    orphans.push({ id: tc.id, name: tc.name, input: tc.input, messageId: tc.messageId || null })
  }
  if (orphans.length === 0) return items

  // Anchor by turn_id first; collect anything that can't be resolved that way.
  const unanchored = []
  for (const orphan of orphans) {
    if (!orphan.messageId) {
      unanchored.push(orphan)
      continue
    }
    const targetIndex = items.findIndex(
      it => it.type === 'message' && it.message?.type === 'assistant' && it.message.metadata?.turn_id === orphan.messageId
    )
    if (targetIndex !== -1) {
      items[targetIndex].orphanedPermissionTools = [...(items[targetIndex].orphanedPermissionTools || []), orphan]
      continue
    }

    // Issue #1746: the owning message may have been merged into a preceding row as a
    // continuation segment — search item.mergedMessages before giving up on turn_id anchoring.
    let attachedToSegment = false
    for (const it of items) {
      if (it.type !== 'message' || !it.mergedMessages) continue
      const seg = it.mergedMessages.find(s => s.metadata?.turn_id === orphan.messageId)
      if (seg) {
        seg.orphanedPermissionTools = [...(seg.orphanedPermissionTools || []), orphan]
        attachedToSegment = true
        break
      }
    }
    if (!attachedToSegment) unanchored.push(orphan)
  }

  if (unanchored.length === 0) return items

  // Fallback: attach to the last assistant item — and, if it's the head of a merged run
  // (Issue #1746), to that run's LAST segment specifically, since "last bubble" means the
  // most recently rendered turn, not the first segment the top-level item happens to be.
  for (let i = items.length - 1; i >= 0; i--) {
    if (items[i].type === 'message' && items[i].message?.type === 'assistant') {
      const mergedMessages = items[i].mergedMessages
      if (mergedMessages && mergedMessages.length > 0) {
        const lastSeg = mergedMessages[mergedMessages.length - 1]
        lastSeg.orphanedPermissionTools = [...(lastSeg.orphanedPermissionTools || []), ...unanchored]
      } else {
        items[i].orphanedPermissionTools = [...(items[i].orphanedPermissionTools || []), ...unanchored]
      }
      return items
    }
  }

  // No assistant bubble yet — push a minimal anchor so the prompt has a slot.
  // This path is defensive only; in practice the model always emits an assistant
  // message before a tool_use. Log a warning if it ever fires.
  console.warn('[issue-1626] attachOrphanedPermissionTools: no assistant bubble found, creating anchor')
  items.push({
    type: 'message',
    message: { type: 'assistant', content: '', metadata: { tool_uses: [] }, timestamp: Date.now() / 1000 },
    attachedTools: [],
    orphanedPermissionTools: unanchored,
  })
  return items
}

function shouldDisplayMessage(message, ctx) {
  // Filter messages that shouldn't be displayed
  const subtype = message.subtype || message.metadata?.subtype

  // Hide system init messages (but NOT status or compact_boundary messages)
  if (message.type === 'system' && subtype === 'init') return false

  // Issue #684: Hide SDK "Tool loaded." messages (internal ToolSearch plumbing)
  if (message.type === 'user' && (message.content || '').trim() === 'Tool loaded.') return false

  // Issue #689: Suppress task lifecycle messages — now displayed inside SubagentTimeline cards
  if (message.type === 'system') {
    const taskSubtype = message.metadata?.subtype
    if (['task_started', 'task_progress', 'task_notification', 'task_updated', 'thinking_tokens'].includes(taskSubtype)) {
      return false
    }
  }

  // Issue #2145: background task snapshots render in the Tasks panel's Background section
  if (message.type === 'system' && subtype === 'background_tasks_changed') return false

  // Issue #1676: Suppress background subagent notifications from the inline message
  // list — they render exclusively in the session-level AgentNotificationStrip.
  if (message.type === 'system' && message.metadata?.subtype === 'agent_notification') {
    return false
  }

  // Note: We do NOT hide 'status' or 'compact_boundary' messages here
  // because they are handled by the compaction event grouping logic above

  // Show client_launched, interrupt, and other system messages
  // (These inform the user about session state changes)

  // Hide result messages (they update session state, don't display)
  if (message.type === 'result') return false

  // Hide permission messages (handled by modal)
  if (message.type === 'permission_request' || message.type === 'permission_response') return false

  // Hide user messages that ONLY contain tool results (no actual user text)
  // These messages exist to deliver tool results which update tool cards inline
  if (message.type === 'user' && message.metadata?.has_tool_results) {
    // Check if this is ONLY tool results (content is just "Tool results: N results")
    const content = message.content || ''
    if (content.match(/^Tool results?: \d+ results?$/i) || content.trim() === '') {
      return false
    }
  }

  // Hide skill-related user messages (skill running notification and skill content)
  // These are displayed within the SkillToolHandler component instead
  if (message.type === 'user') {
    const content = message.content || ''
    // Hide message with <command-message> tag (skill running notification)
    if (content.includes('<command-message>') && content.includes('skill is running')) {
      return false
    }
    // Hide message with skill content (starts with "Base directory for this skill:")
    if (content.startsWith('Base directory for this skill:')) {
      return false
    }
    // Issue #1724: Hide skill re-invocation notice (synthetic bookkeeping shown when
    // a skill's instructions were already loaded earlier in the conversation)
    if (content.startsWith('(Re-invocation of /') && content.includes('previously loaded')) {
      return false
    }
  }

  // Hide slash command-related user messages (command running notification and command content)
  // These are displayed within the SlashCommandToolHandler component instead
  if (message.type === 'user') {
    const content = message.content || ''
    // Hide message with <command-message>, <command-name>, and <command-args> tags (slash command running notification)
    if (content.includes('<command-message>') &&
        content.includes('<command-name>') &&
        content.includes('<command-args>')) {
      return false
    }
    // Hide message with slash command content (contains "ARGUMENTS:" trailer)
    if (content.includes('ARGUMENTS:') &&
        (content.includes('<command-name>') || content.match(/\nARGUMENTS:/))) {
      return false
    }
  }

  // Hide user messages that are task prompts sent to subagents
  // These have parent_tool_use_id set but are not tool results
  // The prompt content is already visible in the Task tool card
  if (message.type === 'user') {
    const metadata = message.metadata || {}
    if (metadata.parent_tool_use_id && !metadata.has_tool_results) {
      return false
    }
  }

  // Issue #1671/#1900: Hide assistant messages forwarded from a subagent (forward_subagent_text).
  // These carry parent_tool_use_id and would otherwise appear as stray top-level bubbles;
  // subagent activity already surfaces via the Task tool card.
  if (message.type === 'assistant') {
    const metadata = message.metadata || {}
    if (metadata.parent_tool_use_id) {
      return false
    }
  }

  // Issue #1350: Hide hook system messages that have been successfully correlated to a
  // parent element (tool node, user/assistant bubble, compaction group). Messages that
  // could NOT be correlated stay visible and render through the SystemMessage pill path.
  if (message.type === 'system') {
    const subtype2 = message.metadata?.subtype
    if (subtype2 === 'hook_started' || subtype2 === 'hook_response') {
      const msgId = message.id || message.message_id
      if (msgId && ctx.isHookMessageAttached(msgId)) {
        return false
      }
    }
  }

  // Issue #1242: Hide signature-only assistant messages emitted in Auto permission mode.
  // These contain a ThinkingBlock with empty thinking text plus a signature blob, no text,
  // no tool_use. Keeping them fragments tool timelines because the grouping walk-back in
  // groupToolsToParentMessages stops at the first empty assistant it finds.
  // Issue #1955: no message in messagesBySession is ever a streaming placeholder anymore —
  // the live-typing preview renders separately via StreamingPreview.vue.
  if (message.type === 'assistant') {
    const meta = message.metadata || {}
    const text = (message.content || '').trim()
    const hasText = text.length > 0 && text !== 'Assistant response'
    const hasThinkingText = (meta.thinking_content || '').trim().length > 0
    const hasTools = (meta.tool_uses || []).length > 0
    if (!hasText && !hasThinkingText && !hasTools) {
      return false
    }
  }

  return true
}

/**
 * Stable virtualizer row key (issue #2030 stage 1b). The default index key would shift the whole
 * measurement cache on a prepend; this identity survives it: the first record's message_id for
 * message and merged-turn rows (and compaction groups), `date:` for separators, `signal:` for
 * subagent signals. Rows without any usable identity fall back to their index.
 */
export function getItemKey(item, index) {
  if (!item) return `i:${index}`
  switch (item.type) {
    case 'message': {
      const id = resolveMessageId(item.message)
      return id != null ? `m:${id}` : `i:${index}`
    }
    case 'compaction': {
      const id = item.messages?.[0] ? resolveMessageId(item.messages[0]) : null
      return id != null ? `c:${id}` : `i:${index}`
    }
    case 'date_separator':
      return item.dateKey != null ? `date:${item.dateKey}` : `i:${index}`
    case 'subagent_signal':
      return item.key ? `signal:${item.key}` : `i:${index}`
    case 'gap':
      return item.key || `i:${index}`
    default:
      return `i:${index}`
  }
}

/**
 * One key per row, guaranteed unique. Most identities are unique by construction, but a row key
 * can legitimately repeat (a date separator for the same day recurs when timestamps run
 * backwards, e.g. a replayed or clock-skewed record). Duplicate keys would make the virtualizer
 * share one measurement/DOM node between rows, so later occurrences get an occurrence suffix.
 */
export function computeItemKeys(items) {
  const seen = new Map()
  return items.map((item, index) => {
    const base = getItemKey(item, index)
    const n = seen.get(base) || 0
    seen.set(base, n + 1)
    return n === 0 ? base : `${base}#${n}`
  })
}
