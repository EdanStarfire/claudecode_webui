import { describe, it, expect, vi } from 'vitest'
import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { setActivePinia, createPinia } from 'pinia'
import { RAW_FIXTURES_ROOT } from './helpers/fixtureEquivalence'
import { createFakeHistoryServer } from './helpers/historyServer'
import { findMatches } from '@/search/messageSearch'

// Issue #2030 (stage 3 PR 3B): drift guard for the SERVER whole-history search
// (backend/history_search.py + FileMessageHistoryReader.search). The browser's `findMatches` over a
// full load is the ground truth: the shared corpus (history_search_cases.json) and the
// per-fixture digests (history_search_fixture_expected.json) are generated HERE
// (`REGEN_SEARCH_EXPECTED=1 npx vitest run ... historySearchParity`) and asserted by
// backend/tests/test_history_search.py against the server.

const apiMock = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn(),
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn().mockReturnValue(null) }))
vi.mock('@/composables/useNotifications', () => ({ notify: vi.fn() }))

const FIXTURES_ROOT = path.resolve(RAW_FIXTURES_ROOT, '..')
const CASES_FILE = path.join(FIXTURES_ROOT, 'history_search_cases.json')
const EXPECTED_FILE = path.join(FIXTURES_ROOT, 'history_search_fixture_expected.json')
const REGEN = !!process.env.REGEN_SEARCH_EXPECTED
const CORPUS = JSON.parse(fs.readFileSync(CASES_FILE, 'utf-8'))
const SID = 'sess-search'
const SCALE_RECORDS = 20000

// Queries run against the committed fixtures (a selective + a broad mix; digests only).
const FIXTURE_QUERIES = [
  'the', 'tool', 'read', 'error', 'file', 'ab', 'json', 'bash', 'session', 'in', 'hello', 'path',
  'true', 'null', 'user', 'content', 'complete', 'toolu', 'permission', 'Café', '😀', '"type"', 'er',
]

async function browserMatches(records, query) {
  setActivePinia(createPinia())
  const { useMessageStore } = await import('@/stores/message')
  const store = useMessageStore()
  const server = createFakeHistoryServer(records, { tailStart: 0 })
  apiMock.get.mockImplementation((url) => server.handle(url))
  await store.loadMessages(SID, null, 0, { window: 'tail', signal: new AbortController().signal })
  return findMatches(SID, query, { messageStore: store }).map(descriptor)
}

function descriptor(m) {
  return [m.messageId, m.toolId, m.field, m.matchStart, m.matchLength, m.snippet, m.occurrenceIndex]
}

function digest(list) {
  const sha = crypto.createHash('sha1')
  for (const d of list) sha.update(JSON.stringify(d) + '\n')
  return { count: list.length, sha1: sha.digest('hex') }
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

// Same tiling as backend/tests/test_history_search.py::_tile (identity fields only).
export function tile(records, total) {
  const reps = Math.floor(total / records.length) + 1
  const out = []
  for (let rep = 0; rep < reps; rep++) {
    const suffix = `-scale-rep${rep}`
    for (const record of records) {
      const copy = JSON.parse(JSON.stringify(record))
      if (copy.message_id) copy.message_id += suffix
      if (copy.tool_use_id) copy.tool_use_id += suffix
      const uses = copy.metadata?.tool_uses
      if (Array.isArray(uses)) for (const use of uses) if (use && use.id) use.id += suffix
      out.push(copy)
    }
  }
  return out
}

async function fixtureResults(records) {
  const out = {}
  for (const query of FIXTURE_QUERIES) out[query] = digest(await browserMatches(records, query))
  return out
}

describe('server search vs browser findMatches (shared corpus)', () => {
  it('corpus is non-empty (fail, never skip)', () => {
    expect(CORPUS.cases.length).toBeGreaterThan(3)
    expect(CORPUS.queries.length).toBeGreaterThan(20)
  })

  it('cases: findMatches == committed expected (regenerates with REGEN_SEARCH_EXPECTED=1)', async () => {
    for (const c of CORPUS.cases) {
      const expected = {}
      for (const query of CORPUS.queries) expected[query] = await browserMatches(c.records, query)
      if (REGEN) c.expected = expected
      else expect(expected, c.name).toEqual(c.expected)
    }
    if (REGEN) fs.writeFileSync(CASES_FILE, JSON.stringify(CORPUS, null, 1) + '\n', 'utf-8')
  })
})

describe('server search vs browser findMatches (committed fixtures)', () => {
  it('fixtures + 20k tiled fixture: digests equal the committed expected', async () => {
    const files = fixtureFiles()
    const result = { fixtures: {}, scale: {} }
    for (const name of Object.keys(files).sort()) {
      result.fixtures[name] = await fixtureResults(readJsonl(files[name]))
    }
    result.scale = await fixtureResults(tile(readJsonl(files.primary), SCALE_RECORDS))
    if (REGEN) {
      fs.writeFileSync(EXPECTED_FILE, JSON.stringify({
        _comment: 'Browser findMatches digests per committed fixture and for the primary fixture ' +
          'tiled to 20k records; generated by historySearchParity.test.js (REGEN_SEARCH_EXPECTED=1), ' +
          'asserted by backend/tests/test_history_search.py against the server search.',
        queries: FIXTURE_QUERIES,
        scale_records: SCALE_RECORDS,
        ...result,
      }, null, 1) + '\n', 'utf-8')
      return
    }
    const committed = JSON.parse(fs.readFileSync(EXPECTED_FILE, 'utf-8'))
    expect(committed.queries).toEqual(FIXTURE_QUERIES)
    expect(result.fixtures).toEqual(committed.fixtures)
    expect(result.scale).toEqual(committed.scale)
  }, 120000)
})
