import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

// Issue #1999 (US1, AC1/AC2): loads recorded raw fixtures (issue #1998's SessionRecorder
// capture format, backend/tests/fixtures/raw/{name}/) for the equivalence harness —
// diffing the live event path (raw_log.jsonl's queue_event stream replayed through
// polling.js's real poll loop) against the REST reload path (rest_history.json replayed
// through message.js's loadMessages()).

const __dirname = path.dirname(fileURLToPath(import.meta.url))

// frontend/src/stores/__tests__/helpers/ -> repo root is 5 levels up.
export const RAW_FIXTURES_ROOT = path.resolve(__dirname, '../../../../../backend/tests/fixtures/raw')

/**
 * Every raw-fixture directory name with a raw_log.jsonl — discovered dynamically (not
 * hardcoded) so a future recorded fixture is picked up automatically. Throws (does not
 * return an empty list) when the directory is missing or has no eligible fixture, so a
 * caller that doesn't guard against an empty result fails loudly instead of silently
 * skipping the equivalence check (AC1's fail-not-skip guard).
 */
export function listRawFixtureNames() {
  if (!fs.existsSync(RAW_FIXTURES_ROOT)) {
    throw new Error(
      `Raw fixtures directory not found: ${RAW_FIXTURES_ROOT}. The equivalence harness ` +
      `requires at least one recorded fixture (see backend/tests/fixtures/raw/) — it does ` +
      `not skip when fixtures are absent.`
    )
  }
  const names = fs.readdirSync(RAW_FIXTURES_ROOT, { withFileTypes: true })
    .filter(entry => entry.isDirectory())
    .map(entry => entry.name)
    .filter(name => fs.existsSync(path.join(RAW_FIXTURES_ROOT, name, 'raw_log.jsonl')))
    .sort()
  if (names.length === 0) {
    throw new Error(
      `No raw fixtures with raw_log.jsonl found under ${RAW_FIXTURES_ROOT}. The equivalence ` +
      `harness requires at least one recorded fixture — it does not skip when fixtures are absent.`
    )
  }
  return names
}

/**
 * Loads one raw fixture's queue_event stream (live path ground truth) and rest_history.json
 * (REST reload path ground truth) — both already produced by #1998's fixture_export.py.
 */
export function loadRawFixture(name) {
  const dir = path.join(RAW_FIXTURES_ROOT, name)
  const rawLogPath = path.join(dir, 'raw_log.jsonl')
  const restHistoryPath = path.join(dir, 'rest_history.json')

  if (!fs.existsSync(rawLogPath)) {
    throw new Error(`raw_log.jsonl not found for fixture '${name}' at ${rawLogPath}`)
  }
  if (!fs.existsSync(restHistoryPath)) {
    throw new Error(`rest_history.json not found for fixture '${name}' at ${restHistoryPath}`)
  }

  const records = fs.readFileSync(rawLogPath, 'utf-8')
    .split('\n')
    .filter(line => line.trim())
    .map(line => JSON.parse(line))

  // The `event` field is a verbatim capture of what was passed to EventQueue.append() —
  // the same envelope shape a browser receives over long-poll (SessionRecorder.record_queue_event).
  const queueEvents = records
    .filter(record => record.kind === 'queue_event')
    .map(record => record.event)

  const restHistory = JSON.parse(fs.readFileSync(restHistoryPath, 'utf-8'))

  const sessionId = queueEvents.find(event => event?.session_id)?.session_id
  if (!sessionId) {
    throw new Error(`No session_id found in the queue_event stream for fixture '${name}'`)
  }

  return { name, queueEvents, restHistory, sessionId }
}

// Timing/provenance-only metadata keys populated independently by each pipeline's own
// wall-clock/reconstruction pass rather than being part of a message's canonical identity —
// stripped before comparison. Discovered by diffing real fixture output (backend/
// message_parser.py's MessageProcessor.process_message()):
//   - `source`: the live path tags every message "sdk" (SessionCoordinator's real-time
//     message callback); the REST reload path re-processes stored JSONL rows and tags them
//     "system" (SessionCoordinator.get_session_messages()) — same underlying message,
//     different pipeline label.
//   - `processed_at`: wall-clock time.time() captured at whichever moment each pipeline
//     happened to process the row (live capture time vs. REST-request time) — never equal
//     between two independent passes over the same data, by construction.
const TIMING_ONLY_KEYS = new Set(['source', 'processed_at'])

function stripTimingOnlyFields(value) {
  if (Array.isArray(value)) {
    return value.map(stripTimingOnlyFields)
  }
  if (value && typeof value === 'object') {
    const result = {}
    for (const [key, val] of Object.entries(value)) {
      if (TIMING_ONLY_KEYS.has(key)) continue
      result[key] = stripTimingOnlyFields(val)
    }
    return result
  }
  return value
}

/** Deep-clones and strips timing-only fields so two independently-produced structures
 * that differ only in provenance/wall-clock metadata compare equal. */
export function normalizeForComparison(value) {
  return stripTimingOnlyFields(value)
}
