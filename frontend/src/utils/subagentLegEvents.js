import { parseTimestamp } from './time'

// The backend's own fallback content for a tool-use-only assistant-turn segment (no text
// parts). Two independent parsers produce this: message_parser.py's live path fills it in
// ("Assistant response"), while session_coordinator.py's reload-reconstruction path leaves
// content as "" instead — the two never agreed. Filtering both here (rather than only in one
// path) is what makes narration display consistent between live streaming and page reload.
const PLACEHOLDER_CONTENT = 'Assistant response'

function hasRenderableNarration(msg) {
  const hasText = !!(msg.content && msg.content.trim().length > 0 && msg.content !== PLACEHOLDER_CONTENT)
  const hasThinking = !!msg.thinking
  return hasText || hasThinking
}

/**
 * Issue #1746 (stage: subagents) follow-up (user feedback): merge a leg's narration messages
 * and child tool calls into chronologically-ordered runs, grouping consecutive same-kind items
 * together — a run of narration messages, or a run of tool calls — so the UI can alternate
 * between them the way they actually happened (thought, tool, tool, message, tool, ...) instead
 * of stacking "all narration" above "all tools" regardless of when each one actually occurred.
 *
 * Narration entries with no renderable content are dropped entirely (not just hidden) — a
 * tool-only assistant-turn segment carries nothing worth showing, and leaving an empty entry in
 * the list would still consume visual gap space and produce a blank div.
 */
export function buildLegEventRuns(narrationEntries, toolCalls) {
  const items = []
  for (const msg of narrationEntries || []) {
    if (!hasRenderableNarration(msg)) continue
    items.push({ kind: 'narration', ts: parseTimestamp(msg.timestamp).getTime(), data: msg })
  }
  for (const tc of toolCalls || []) {
    items.push({ kind: 'tool', ts: parseTimestamp(tc.timestamp ?? tc.created_at).getTime(), data: tc })
  }
  items.sort((a, b) => a.ts - b.ts)

  const runs = []
  for (const item of items) {
    const last = runs[runs.length - 1]
    if (last && last.kind === item.kind) {
      last.items.push(item.data)
    } else {
      runs.push({ kind: item.kind, items: [item.data] })
    }
  }
  return runs
}
