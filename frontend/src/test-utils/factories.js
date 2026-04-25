export function makeSession(overrides = {}) {
  return {
    session_id: 'sess-1',
    project_id: 'proj-1',
    name: 'Test Session',
    state: 'active',
    is_processing: false,
    current_permission_mode: 'default',
    working_directory: '/tmp',
    order: 0,
    child_minion_ids: [],
    is_minion: false,
    ...overrides
  }
}

export function makeProject(overrides = {}) {
  return {
    project_id: 'proj-1',
    name: 'Test Project',
    working_directory: '/tmp',
    session_ids: [],
    is_expanded: true,
    is_multi_agent: false,
    order: 0,
    ...overrides
  }
}

export function makeMessage(overrides = {}) {
  return {
    type: 'assistant',
    content: 'hello',
    timestamp: 1700000000,
    session_id: 'sess-1',
    ...overrides
  }
}

export function makeToolCall(overrides = {}) {
  return {
    id: 'tool-1',
    tool_use_id: 'use-1',
    name: 'Bash',
    input: { command: 'ls' },
    status: 'pending',
    session_id: 'sess-1',
    ...overrides
  }
}

export function makeMinion(overrides = {}) {
  return {
    minion_id: 'min-1',
    name: 'Minion-1',
    legion_id: 'leg-1',
    role: 'worker',
    capabilities: [],
    ...overrides
  }
}

export function makeComm(overrides = {}) {
  return {
    comm_id: 'comm-1',
    legion_id: 'leg-1',
    from_minion_id: 'min-1',
    to_minion_id: 'min-2',
    summary: 'test',
    content: 'test content',
    comm_type: 'info',
    timestamp: 1700000000,
    ...overrides
  }
}
