import { useSessionStore } from '@/stores/session'
import { api } from '@/utils/api'

/**
 * Shared frontend debug ring buffer (issue #1931).
 *
 * Module-level singleton array, not a Pinia store — mirrors the non-reactive
 * internal-state precedent already used in message.js (_deltaBuffers,
 * _hookCorrelationCache). Nothing in the UI needs reactive access to buffer
 * contents.
 */

const MAX_EVENTS = 1000
const MAX_PAYLOAD_CHARS = 2000
const FLUSH_DEBOUNCE_MS = 3000

let _buffer = []
let _flushing = false
let _lastFlushCompletedAt = 0

function truncate(data) {
  let json
  try {
    json = JSON.stringify(data)
  } catch (err) {
    return { truncated: true, preview: `[unserializable: ${err.message}]` }
  }
  if (json === undefined) return null
  if (json.length <= MAX_PAYLOAD_CHARS) return data
  return { truncated: true, preview: json.slice(0, MAX_PAYLOAD_CHARS) }
}

export function pushDebugEvent(source, tag, data) {
  _buffer.push({ ts: Date.now(), source, tag, data: truncate(data) })
  if (_buffer.length > MAX_EVENTS) {
    _buffer.splice(0, _buffer.length - MAX_EVENTS)
  }
}

/**
 * @returns {Promise<boolean>} true once a submission was actually attempted
 * (regardless of network success/failure), false if skipped by the in-flight
 * or debounce guard — callers use this to avoid showing a "sent" confirmation
 * for a flush that never left the browser.
 */
export async function flushDebugBuffer(reason) {
  const now = Date.now()
  if (_flushing || (now - _lastFlushCompletedAt) < FLUSH_DEBOUNCE_MS) {
    return false
  }
  _flushing = true
  try {
    const sessionStore = useSessionStore()
    await api.post('/api/debug/client-buffer', {
      session_id: sessionStore.currentSessionId || null,
      browser: { user_agent: navigator.userAgent },
      submitted_at: new Date().toISOString(),
      reason,
      // Snapshot, not drain — the buffer keeps rolling forward regardless of
      // submission outcome, so a failed submission never loses data for a retry.
      events: _buffer.slice(),
    })
  } catch (err) {
    console.warn('[useDebugBuffer] flush failed:', err.message || err)
  } finally {
    _flushing = false
    _lastFlushCompletedAt = Date.now()
  }
  return true
}

export function _resetDebugBufferForTests() {
  _buffer = []
  _flushing = false
  _lastFlushCompletedAt = 0
}
