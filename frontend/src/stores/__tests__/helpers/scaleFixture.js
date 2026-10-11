import fs from 'node:fs'
import path from 'node:path'
import { RAW_FIXTURES_ROOT } from './fixtureEquivalence.js'

// Issue #2131 (AC3): scales a committed rest_history.json's `messages` array to
// `targetCount`+ entries by replaying it repeatedly, rewriting identity fields
// (`message_id`, `tool_use_id`, `turn_id`, `metadata.retry_message_id`) with a
// per-repetition `-scale-repN` suffix and shifting timestamps forward each repetition —
// the same technique backend/tests/fixtures/scale_fixture.py (stage 1b, #2001) uses for its
// own (differently-shaped) queue_event stream. This is NOT a reuse of that generator: it
// scales the REST-reload `messages` array shape the frontend's loadMessages() cold-load path
// actually consumes, which scale_fixture.py never produces. See issue #2131's plan §2.

/** Deep-rewrites one record's identity-bearing fields with `suffix`, and shifts its
 * timestamp fields by `timeShift` seconds, so repetition N's records never collide with
 * repetition M's under the store's dedup/identity indices. */
function _rewriteRecord(record, suffix, timeShift) {
  const copy = JSON.parse(JSON.stringify(record))
  if (copy.message_id) copy.message_id = `${copy.message_id}${suffix}`
  if (copy.tool_use_id) copy.tool_use_id = `${copy.tool_use_id}${suffix}`
  if (copy.turn_id) copy.turn_id = `${copy.turn_id}${suffix}`
  if (copy.metadata?.retry_message_id) {
    copy.metadata.retry_message_id = `${copy.metadata.retry_message_id}${suffix}`
  }
  if (copy.metadata?.uuid) copy.metadata.uuid = `${copy.metadata.uuid}${suffix}`
  if (typeof copy.timestamp === 'number') copy.timestamp += timeShift
  if (typeof copy.created_at === 'number') copy.created_at += timeShift
  return copy
}

/**
 * Scales `messages` (a rest_history.json `messages` array) up to at least `targetCount`
 * entries by repeating the whole sequence, with every repetition's identity fields rewritten
 * so none of them collide with any other repetition's (see _rewriteRecord above).
 */
export function scaleRestHistoryMessages(messages, targetCount) {
  if (!messages.length) {
    throw new Error('scaleRestHistoryMessages: cannot scale an empty messages array')
  }
  const repsNeeded = Math.ceil(targetCount / messages.length)
  const firstTimestamp = messages[0].timestamp ?? 0
  const lastTimestamp = messages[messages.length - 1].timestamp ?? firstTimestamp
  // +1 so consecutive repetitions' timestamp ranges never exactly touch.
  const repSpan = Math.max(lastTimestamp - firstTimestamp, 0) + 1

  const scaled = []
  for (let rep = 0; rep < repsNeeded; rep++) {
    const suffix = `-scale-rep${rep}`
    const timeShift = rep * repSpan
    for (const original of messages) {
      scaled.push(_rewriteRecord(original, suffix, timeShift))
    }
  }
  return scaled
}

/** Loads `fixtureName`'s committed rest_history.json and scales its `messages` array. */
export function loadScaledRestHistory(fixtureName, targetCount) {
  const restHistoryPath = path.join(RAW_FIXTURES_ROOT, fixtureName, 'rest_history.json')
  const restHistory = JSON.parse(fs.readFileSync(restHistoryPath, 'utf-8'))
  return scaleRestHistoryMessages(restHistory.messages, targetCount)
}
