import { describe, it, expect, vi } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
import { setActivePinia, createPinia } from 'pinia'
import { RAW_FIXTURES_ROOT } from './helpers/fixtureEquivalence'
import { createFakeHistoryServer } from './helpers/historyServer'
import { buildDisplayableItems } from '@/utils/displayableItems'
import { resolveMessageId } from '@/search/messageSearch'

// Issue #2030 (stage 2 PR A): drift guard for the SERVER history index
// (backend/history_index.py). The browser's turn predicate (displayable user rows of
// buildDisplayableItems) and compaction grouping must produce exactly the turns/compaction keys
// the server reducer expects — on the shared case file and on every committed fixture. The next
// PR (browser index store) consumes the same files.

const apiMock = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn(),
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn().mockReturnValue(null) }))
vi.mock('@/composables/useNotifications', () => ({ notify: vi.fn() }))

const FIXTURES_ROOT = path.resolve(RAW_FIXTURES_ROOT, '..')
const CASES = JSON.parse(
  fs.readFileSync(path.join(FIXTURES_ROOT, 'history_index_cases.json'), 'utf-8')
).cases
const FIXTURE_EXPECTED = JSON.parse(
  fs.readFileSync(path.join(FIXTURES_ROOT, 'history_index_fixture_expected.json'), 'utf-8')
).fixtures
const SID = 'sess-index'

async function freshStore() {
  setActivePinia(createPinia())
  const { useMessageStore } = await import('@/stores/message')
  return useMessageStore()
}

/** Loads `records` the way the browser does (live/load path, or the archive path) and returns
 * what the browser would show: turn ids (displayable user rows) and compaction group keys. */
async function browserIndex(records, { archive = false } = {}) {
  const store = await freshStore()
  if (archive) {
    store.setArchiveMessages(SID, records)
  } else {
    const server = createFakeHistoryServer(records, { tailStart: 0 })
    apiMock.get.mockImplementation((url) => server.handle(url))
    await store.loadMessages(SID, null, 0, { window: 'tail', signal: new AbortController().signal })
  }
  const items = buildDisplayableItems({
    sessionId: SID,
    messages: store.messagesBySession.get(SID) || [],
    toolCalls: store.toolCallsBySession.get(SID) || [],
    legEntries: store.allTaskLegEntriesForSession(SID),
    getTaskIdForLaunchToolUse: store.getTaskIdForLaunchToolUse,
    isHookMessageAttached: (id) => store.isHookMessageAttached(SID, id),
    historyComplete: true,
  })
  return {
    turns: items
      .filter(item => item.type === 'message' && item.message?.type === 'user')
      .map(item => resolveMessageId(item.message)),
    compactions: items
      .filter(item => item.type === 'compaction')
      .map(item => resolveMessageId(item.messages[0])),
  }
}

function readJsonl(file) {
  return fs.readFileSync(file, 'utf-8').split('\n').filter(l => l.trim()).map(l => JSON.parse(l))
}

function fixtureFiles() {
  const out = {}
  for (const root of [FIXTURES_ROOT, RAW_FIXTURES_ROOT]) {
    for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
      const file = path.join(root, entry.name, 'messages.jsonl')
      if (entry.isDirectory() && fs.existsSync(file)) out[entry.name] = file
    }
  }
  return out
}

describe('server history index vs browser predicate (shared case file)', () => {
  it('case file is non-empty (fail, never skip)', () => {
    expect(CASES.length).toBeGreaterThan(10)
  })

  it.each(CASES.map(c => [c.name, c]))('%s', async (_name, c) => {
    const result = await browserIndex(c.records, { archive: c.archive })
    expect(result.turns).toEqual(c.expected.turns)
    expect(result.compactions).toEqual(c.expected.compactions)
  })
})

describe('server history index vs browser predicate (committed fixtures)', () => {
  const files = fixtureFiles()

  it('covers every fixture the server test covers (fail, never skip)', () => {
    expect(Object.keys(files).sort()).toEqual(Object.keys(FIXTURE_EXPECTED).sort())
  })

  it.each(Object.keys(files).sort().map(n => [n]))('%s', async (name) => {
    const records = readJsonl(files[name])
    const result = await browserIndex(records)
    expect(result.turns).toEqual(FIXTURE_EXPECTED[name].turns)
    expect(result.compactions).toEqual(FIXTURE_EXPECTED[name].compactions)
  })

  it('20k-record tiled fixture: browser result is N copies of the single-repetition result', async () => {
    const records = readJsonl(files.primary)
    const single = await browserIndex(records)
    const reps = Math.floor(20000 / records.length) + 1
    const tiled = []
    for (let rep = 0; rep < reps; rep++) {
      for (const record of records) {
        tiled.push({ ...record, message_id: `${record.message_id}-scale-rep${rep}` })
      }
    }
    expect(tiled.length).toBeGreaterThanOrEqual(20000)
    const result = await browserIndex(tiled)
    const expand = ids => Array.from({ length: reps }, (_, rep) => ids.map(i => `${i}-scale-rep${rep}`)).flat()
    expect(result.turns).toEqual(expand(single.turns))
    expect(result.compactions).toEqual(expand(single.compactions))
    expect(single.turns).toEqual(FIXTURE_EXPECTED.primary.turns)
  })
})
