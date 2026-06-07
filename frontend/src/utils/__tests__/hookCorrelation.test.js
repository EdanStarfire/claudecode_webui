import { describe, it, expect } from 'vitest'
import { correlateHooks, aggregateHookStatus } from '@/utils/hookCorrelation'

// ── Fixture helpers ────────────────────────────────────────────────────────────

let _ts = 1000000

function ts() {
  return _ts++
}

function clientLaunched(id = 'msg-cl') {
  return {
    type: 'system',
    message_id: id,
    timestamp: ts(),
    metadata: { subtype: 'client_launched' },
    content: 'Claude Code Launched',
  }
}

function assistantMsg(id, toolUses = []) {
  return {
    type: 'assistant',
    message_id: id,
    timestamp: ts(),
    content: 'I will do that.',
    metadata: {
      has_tool_uses: toolUses.length > 0,
      tool_uses: toolUses,
    },
  }
}

function userMsg(id, content = 'Do the thing') {
  return {
    type: 'user',
    message_id: id,
    timestamp: ts(),
    content,
    metadata: {},
  }
}

function toolResultMsg(toolUseId) {
  return {
    type: 'user',
    message_id: `tr-${toolUseId}`,
    timestamp: ts(),
    content: 'Tool results: 1 result',
    metadata: {
      has_tool_results: true,
      tool_results: [{ tool_use_id: toolUseId, content: 'ok', is_error: false }],
    },
  }
}

function hookStarted(hookId, hookEvent, hookName = 'my-hook', id = null) {
  return {
    type: 'system',
    message_id: id || `hook-started-${hookId}`,
    timestamp: ts(),
    content: '',
    metadata: {
      subtype: 'hook_started',
      init_data: { hook_id: hookId, hook_event: hookEvent, hook_name: hookName },
    },
  }
}

function hookResponse(hookId, hookEvent, exitCode = 0, hookName = 'my-hook', id = null) {
  return {
    type: 'system',
    message_id: id || `hook-response-${hookId}`,
    timestamp: ts(),
    content: '',
    metadata: {
      subtype: 'hook_response',
      init_data: {
        hook_id: hookId,
        hook_event: hookEvent,
        hook_name: hookName,
        exit_code: exitCode,
        outcome: exitCode === 0 ? 'approved' : 'failed',
      },
    },
  }
}

function compactingStatus(id = 'msg-compact') {
  return {
    type: 'system',
    message_id: id,
    timestamp: ts(),
    content: '',
    metadata: { subtype: 'status', init_data: { status: 'compacting' } },
  }
}

// ── Tests ──────────────────────────────────────────────────────────────────────

describe('correlateHooks', () => {
  it('returns empty maps for a session with no hooks', () => {
    const messages = [
      clientLaunched(),
      userMsg('u1'),
      assistantMsg('a1', [{ id: 'tool-A', name: 'Edit', input: {} }]),
      toolResultMsg('tool-A'),
    ]
    const result = correlateHooks(messages)
    expect(result.hooksByToolId.size).toBe(0)
    expect(result.hooksByMessageId.size).toBe(0)
    expect(result.attachedHookMessageIds.size).toBe(0)
    expect(result.unattachedHookMessageIds.size).toBe(0)
  })

  it('attaches PostToolUse hook to the completed tool', () => {
    const messages = [
      clientLaunched(),
      userMsg('u1'),
      assistantMsg('a1', [{ id: 'tool-A', name: 'Edit', input: {} }]),
      toolResultMsg('tool-A'),
      hookStarted('h1', 'PostToolUse', 'lint-after', 'hs-h1'),
      hookResponse('h1', 'PostToolUse', 0, 'lint-after', 'hr-h1'),
    ]
    const result = correlateHooks(messages)
    expect(result.hooksByToolId.has('tool-A')).toBe(true)
    const hooks = result.hooksByToolId.get('tool-A')
    expect(hooks).toHaveLength(1)
    expect(hooks[0].hook_name).toBe('lint-after')
    expect(hooks[0].hook_event).toBe('PostToolUse')
    expect(hooks[0].status).toBe('success')
    expect(result.attachedHookMessageIds.has('hs-h1')).toBe(true)
    expect(result.attachedHookMessageIds.has('hr-h1')).toBe(true)
  })

  it('attaches PreToolUse hook to the oldest pending tool', () => {
    const messages = [
      userMsg('u1'),
      assistantMsg('a1', [{ id: 'tool-A', name: 'Bash', input: {} }]),
      hookStarted('h1', 'PreToolUse', 'cmd-validate', 'hs-h1'),
      hookResponse('h1', 'PreToolUse', 0, 'cmd-validate', 'hr-h1'),
      toolResultMsg('tool-A'),
    ]
    const result = correlateHooks(messages)
    expect(result.hooksByToolId.has('tool-A')).toBe(true)
    const hooks = result.hooksByToolId.get('tool-A')
    expect(hooks[0].hook_event).toBe('PreToolUse')
    expect(hooks[0].status).toBe('success')
  })

  it('co-locates PreToolUse + PostToolUse on the same tool', () => {
    const messages = [
      userMsg('u1'),
      assistantMsg('a1', [{ id: 'tool-A', name: 'Edit', input: {} }]),
      hookStarted('h1', 'PreToolUse', 'format-check', 'hs-h1'),
      hookResponse('h1', 'PreToolUse', 0, 'format-check', 'hr-h1'),
      toolResultMsg('tool-A'),
      hookStarted('h2', 'PostToolUse', 'lint-after', 'hs-h2'),
      hookResponse('h2', 'PostToolUse', 0, 'lint-after', 'hr-h2'),
    ]
    const result = correlateHooks(messages)
    const hooks = result.hooksByToolId.get('tool-A')
    expect(hooks).toHaveLength(2)
    expect(hooks[0].hook_event).toBe('PreToolUse')
    expect(hooks[1].hook_event).toBe('PostToolUse')
    expect(hooks[0].status).toBe('success')
    expect(hooks[1].status).toBe('success')
  })

  it('marks hook_response as failure when exit_code is non-zero', () => {
    const messages = [
      userMsg('u1'),
      assistantMsg('a1', [{ id: 'tool-A', name: 'Bash', input: {} }]),
      toolResultMsg('tool-A'),
      hookStarted('h1', 'PostToolUse', 'notify-slack', 'hs-h1'),
      hookResponse('h1', 'PostToolUse', 1, 'notify-slack', 'hr-h1'),
    ]
    const result = correlateHooks(messages)
    const hooks = result.hooksByToolId.get('tool-A')
    expect(hooks[0].status).toBe('failure')
    expect(hooks[0].exit_code).toBe(1)
  })

  it('keeps hook as pending when only hook_started seen (no hook_response)', () => {
    const messages = [
      userMsg('u1'),
      assistantMsg('a1', [{ id: 'tool-A', name: 'Write', input: {} }]),
      toolResultMsg('tool-A'),
      hookStarted('h1', 'PostToolUse', 'upload-artifact', 'hs-h1'),
      // No hook_response
    ]
    const result = correlateHooks(messages)
    const hooks = result.hooksByToolId.get('tool-A')
    expect(hooks[0].status).toBe('pending')
    expect(result.attachedHookMessageIds.has('hs-h1')).toBe(true)
  })

  it('attaches multiple hooks to the same tool', () => {
    const messages = [
      userMsg('u1'),
      assistantMsg('a1', [{ id: 'tool-A', name: 'Bash', input: {} }]),
      toolResultMsg('tool-A'),
      hookStarted('h1', 'PostToolUse', 'hook-one', 'hs-h1'),
      hookResponse('h1', 'PostToolUse', 0, 'hook-one', 'hr-h1'),
      hookStarted('h2', 'PostToolUse', 'hook-two', 'hs-h2'),
      hookResponse('h2', 'PostToolUse', 0, 'hook-two', 'hr-h2'),
    ]
    const result = correlateHooks(messages)
    const hooks = result.hooksByToolId.get('tool-A')
    expect(hooks).toHaveLength(2)
    expect(hooks[0].hook_name).toBe('hook-one')
    expect(hooks[1].hook_name).toBe('hook-two')
  })

  it('handles two consecutive same-name tools via FIFO queue (risk #5)', () => {
    const messages = [
      userMsg('u1'),
      // Both Edit calls appear in same assistant message
      assistantMsg('a1', [
        { id: 'edit-A', name: 'Edit', input: { path: 'a.txt' } },
        { id: 'edit-B', name: 'Edit', input: { path: 'b.txt' } },
      ]),
      hookStarted('h1', 'PreToolUse', 'format-check', 'hs-h1'),
      hookResponse('h1', 'PreToolUse', 0, 'format-check', 'hr-h1'),
      toolResultMsg('edit-A'),
      hookStarted('h2', 'PostToolUse', 'lint-A', 'hs-h2'),
      hookResponse('h2', 'PostToolUse', 0, 'lint-A', 'hr-h2'),
      hookStarted('h3', 'PreToolUse', 'format-check', 'hs-h3'),
      hookResponse('h3', 'PreToolUse', 0, 'format-check', 'hr-h3'),
      toolResultMsg('edit-B'),
      hookStarted('h4', 'PostToolUse', 'lint-B', 'hs-h4'),
      hookResponse('h4', 'PostToolUse', 0, 'lint-B', 'hr-h4'),
    ]
    const result = correlateHooks(messages)
    // PreToolUse h1 → edit-A (oldest pending)
    const hooksA = result.hooksByToolId.get('edit-A')
    expect(hooksA).toHaveLength(2)  // Pre + Post for edit-A
    expect(hooksA.find(h => h.hook_event === 'PreToolUse')).toBeTruthy()
    expect(hooksA.find(h => h.hook_event === 'PostToolUse' && h.hook_name === 'lint-A')).toBeTruthy()

    // PreToolUse h3 → edit-B
    const hooksB = result.hooksByToolId.get('edit-B')
    expect(hooksB).toHaveLength(2)
    expect(hooksB.find(h => h.hook_event === 'PreToolUse')).toBeTruthy()
    expect(hooksB.find(h => h.hook_event === 'PostToolUse' && h.hook_name === 'lint-B')).toBeTruthy()
  })

  it('attaches UserPromptSubmit hook to the preceding user message', () => {
    const messages = [
      userMsg('u1', 'Refactor the auth handler.'),
      hookStarted('h1', 'UserPromptSubmit', 'prompt-scan', 'hs-h1'),
      hookResponse('h1', 'UserPromptSubmit', 0, 'prompt-scan', 'hr-h1'),
      assistantMsg('a1'),
    ]
    const result = correlateHooks(messages)
    expect(result.hooksByMessageId.has('u1')).toBe(true)
    const hooks = result.hooksByMessageId.get('u1')
    expect(hooks[0].hook_event).toBe('UserPromptSubmit')
    expect(hooks[0].status).toBe('success')
  })

  it('attaches Stop hook to the preceding assistant message', () => {
    const messages = [
      userMsg('u1'),
      assistantMsg('a1'),
      hookStarted('h1', 'Stop', 'turn-complete', 'hs-h1'),
      hookResponse('h1', 'Stop', 0, 'turn-complete', 'hr-h1'),
    ]
    const result = correlateHooks(messages)
    expect(result.hooksByMessageId.has('a1')).toBe(true)
    const hooks = result.hooksByMessageId.get('a1')
    expect(hooks[0].hook_event).toBe('Stop')
  })

  it('attaches SessionStart hook to client_launched', () => {
    const messages = [
      clientLaunched('cl-1'),
      hookStarted('h1', 'SessionStart', 'env-validate', 'hs-h1'),
      hookResponse('h1', 'SessionStart', 0, 'env-validate', 'hr-h1'),
    ]
    const result = correlateHooks(messages)
    expect(result.hooksOnClientLaunched).toHaveLength(1)
    expect(result.hooksOnClientLaunched[0].hook_event).toBe('SessionStart')
    expect(result.attachedHookMessageIds.has('hs-h1')).toBe(true)
  })

  it('attaches PreCompact hook to the current compaction group', () => {
    const messages = [
      userMsg('u1'),
      compactingStatus('cs-1'),
      hookStarted('h1', 'PreCompact', 'archive-before-compact', 'hs-h1'),
      hookResponse('h1', 'PreCompact', 1, 'archive-before-compact', 'hr-h1'),
    ]
    const result = correlateHooks(messages)
    expect(result.hooksByCompactionIndex.has(0)).toBe(true)
    const hooks = result.hooksByCompactionIndex.get(0)
    expect(hooks[0].hook_event).toBe('PreCompact')
    expect(hooks[0].status).toBe('failure')
  })

  it('falls back to unattached when no anchor available', () => {
    // Hook fires with no tool call, no user/assistant message before it
    const messages = [
      hookStarted('h1', 'PostToolUse', 'orphan-hook', 'hs-h1'),
      hookResponse('h1', 'PostToolUse', 0, 'orphan-hook', 'hr-h1'),
    ]
    const result = correlateHooks(messages)
    expect(result.hooksByToolId.size).toBe(0)
    expect(result.unattachedHookMessageIds.has('hs-h1')).toBe(true)
    // hook_response has no matching started → also unattached
    expect(result.unattachedHookMessageIds.has('hr-h1')).toBe(true)
  })

  it('tracks attachedHookMessageIds correctly for both started and response', () => {
    const messages = [
      userMsg('u1'),
      assistantMsg('a1', [{ id: 'tool-A', name: 'Read', input: {} }]),
      toolResultMsg('tool-A'),
      hookStarted('h1', 'PostToolUse', 'log', 'hs-h1'),
      hookResponse('h1', 'PostToolUse', 0, 'log', 'hr-h1'),
    ]
    const result = correlateHooks(messages)
    expect(result.attachedHookMessageIds.has('hs-h1')).toBe(true)
    expect(result.attachedHookMessageIds.has('hr-h1')).toBe(true)
    expect(result.unattachedHookMessageIds.has('hs-h1')).toBe(false)
    expect(result.unattachedHookMessageIds.has('hr-h1')).toBe(false)
  })
})

describe('aggregateHookStatus', () => {
  it('returns null for empty array', () => {
    expect(aggregateHookStatus([])).toBe(null)
    expect(aggregateHookStatus(null)).toBe(null)
  })

  it('returns success when all succeed', () => {
    const hooks = [{ status: 'success' }, { status: 'success' }]
    expect(aggregateHookStatus(hooks)).toBe('success')
  })

  it('returns pending when any pending and no failure', () => {
    const hooks = [{ status: 'success' }, { status: 'pending' }]
    expect(aggregateHookStatus(hooks)).toBe('pending')
  })

  it('returns failure when all fail', () => {
    const hooks = [{ status: 'failure' }, { status: 'failure' }]
    expect(aggregateHookStatus(hooks)).toBe('failure')
  })

  it('returns mixed when failure and success co-exist', () => {
    const hooks = [{ status: 'success' }, { status: 'failure' }]
    expect(aggregateHookStatus(hooks)).toBe('mixed')
  })
})
