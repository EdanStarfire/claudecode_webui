import { describe, it, expect } from 'vitest'

// Issue #2030 stage 4 PR 4b: loaded history records are immutable (markRaw messages) and tool entries
// are shallowReactive with REPLACE-only nested values. A new in-place write to one of their fields
// from anywhere other than the allowlisted writers silently stops re-rendering (or throws on a
// frozen record), so a new assignment fails this test until it is moved into stores/message.js
// (or consciously allowlisted here with a justification).

const sources = import.meta.glob('/src/**/*.{js,vue}', { query: '?raw', import: 'default', eager: true })

const ASSIGN = '\\s*(?:=|\\+=|-=|\\|\\|=|&&=|\\?\\?=)(?!=)'

// Fields that only tool entries / message records carry: no allowed writer outside these files.
const TOOL_ONLY = ['permissionRequestId', 'permissionDecision', 'appliedUpdates', 'autoApprovedReason',
  'senderAttachments', '_isOrphaned', '_orphanedInfo', 'isExpanded', 'messageId', 'answers', 'suggestions',
  'parent_tool_use_id', 'agentId']
const TOOL_ONLY_WRITERS = ['/src/stores/message.js', '/src/utils/historyMerge.js']

// Generic names: files known to assign them to something that is NOT a store tool entry / message.
const GENERIC = ['status', 'result', 'input', 'timestamp', 'content', 'message_id', 'metadata', 'tool_uses']
const GENERIC_ALLOWED = {
  '/src/stores/message.js': 'the store itself (tool writers, leg state, streaming preview)',
  '/src/utils/historyMerge.js': 'mergeToolCallFields',
  '/src/utils/hookCorrelation.js': 'its own hook-indicator result objects, rebuilt per run',
  '/src/components/layout/RestartModal.vue': 'local modal state',
  '/src/utils/api.js': 'APIError',
  '/src/components/tools/TaskGetToolHandler.vue': 'a local parse result',
  '/src/stores/task.js': 'task store records',
  '/src/stores/archiveTabs.js': 'queue records of its own state',
  '/src/stores/polling.js': 'an outbound payload',
  '/src/stores/queue.js': 'an outbound payload',
  '/src/components/settings/sections/SecretGeneralSection.vue': 'an outbound payload',
  '/src/components/configuration/McpConfigTab.vue': 'a local form',
  '/src/composables/useNotifications.js': 'a WebAudio node',
}
// Bulk/bracket writes onto their own (session / resource / settings) records, not history entries.
const UNRELATED_BULK_WRITERS = ['/src/stores/session.js', '/src/stores/resource.js',
  '/src/components/settings/sections/GeneralSection.vue']
// Pipeline items are rebuilt on every run (never part of the store).
const PIPELINE_FIELDS = ['attachedTools', 'isMessageIdContinuation', 'orphanedPermissionTools']

function findAssignments(fields, { allowed, only }) {
  const re = new RegExp(`\\.(?:${fields.join('|')})${ASSIGN}`)
  const hits = []
  for (const [path, text] of Object.entries(sources)) {
    if (path.includes('/__tests__/') || path.includes('/test-utils/')) continue
    if (allowed.includes(path)) continue
    if (only && !only.includes(path)) continue
    text.split('\n').forEach((line, i) => {
      if (/^\s*(\/\/|\*|\/\*)/.test(line)) return
      if (re.test(line)) hits.push(`${path}:${i + 1}: ${line.trim()}`)
    })
  }
  return hits
}

describe('writers of loaded-history fields', () => {
  it('sees the sources (guards against the glob silently matching nothing)', () => {
    expect(Object.keys(sources)).toContain('/src/stores/message.js')
    expect(Object.keys(sources).length).toBeGreaterThan(100)
  })

  it('tool-only fields are assigned only by stores/message.js and utils/historyMerge.js', () => {
    expect(findAssignments(TOOL_ONLY, { allowed: TOOL_ONLY_WRITERS })).toEqual([])
  })

  it('generic record fields (status/result/input/timestamp/content/...) are assigned only in the allowlisted files', () => {
    expect(findAssignments(GENERIC, { allowed: Object.keys(GENERIC_ALLOWED) })).toEqual([])
  })

  it('pipeline-item fields are only assigned by the row pipeline', () => {
    expect(findAssignments(PIPELINE_FIELDS, { allowed: ['/src/utils/displayableItems.js'] })).toEqual([])
  })

  it('no bulk/bracket/delete writes onto tool entries or messages outside the allowlisted files', () => {
    const patterns = [
      /Object\.assign\(\s*(?:tool|toolCall|tc|entry|existing|liveToolCall|fullToolCall|msg|message|record)\b/,
      /\bdelete\s+(?:tool|toolCall|tc|entry|existing|msg|message)\.[A-Za-z_]/,
      /\b(?:tool|toolCall|tc|entry|existing|current|msg)\[[^\]]+\]\s*(?:=|\+=|\|\|=|\?\?=)(?!=)/,
    ]
    const offenders = []
    for (const [path, text] of Object.entries(sources)) {
      if (path.includes('/__tests__/') || TOOL_ONLY_WRITERS.includes(path) || UNRELATED_BULK_WRITERS.includes(path)) continue
      text.split('\n').forEach((line, i) => {
        if (/^\s*(\/\/|\*|\/\*)/.test(line)) return
        if (patterns.some(re => re.test(line))) offenders.push(`${path}:${i + 1}: ${line.trim()}`)
      })
    }
    expect(offenders).toEqual([])
  })

  it('no code outside the store writes through the store Maps directly', () => {
    const re = /(?:messagesBySession|toolCallsBySession|islandBySession)\.(?:value\.)?(?:set|delete|clear)\(/
    const offenders = Object.entries(sources)
      .filter(([path]) => !path.includes('/__tests__/') && path !== '/src/stores/message.js')
      .filter(([, text]) => re.test(text))
      .map(([path]) => path)
    expect(offenders).toEqual([])
  })
})
