import { describe, it, expect, beforeAll } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { usePollingStore } from '../polling'
import { loadEventRegistryFixture } from './helpers/eventRegistryFixture'

// QUEUE_AUDIT-only types are a wake signal, not browser poll data (shared/event_registry.py's
// own module docstring / #2063's AC's "edge cases" note) — never expected to have a browser
// handler, on either stream.
const AUDIT_ONLY_ALLOWLIST = new Set(['audit_event', 'audit_event_flush'])

// Stage 2b-B (#2065) removes this UI handler (nothing server-side produces it) — tracked
// here, not swallowed by a looser check, so this exception disappears as part of that
// stage's own diff instead of lingering silently.
const PENDING_2B_B_EXTRA_UI_HANDLER = new Set(['sessions_list'])

// Stage 2b-B (#2065 AC7) adds these two UI handlers. Until then they're registered,
// intentionally-unhandled "orphan" types per #2063 AC7 / .claude/API_REFERENCE.md.
const PENDING_2B_B_MISSING_UI_HANDLER = new Set(['server_restarting', 'session_self_restart'])

// NOT part of #2065's own ACs — found during this stage's own audit while building this
// test. backend/routers/secrets.py's POST /api/sessions/{id}/events lets the Docker proxy
// sidecar legitimately emit `secret_refresh_failed` (registered on QUEUE_SESSION with a
// `data`-wrapped shape, shared/event_registry.py:50-54) and the registry's catch-all
// `proxy_event` type (shared/event_registry.py:84-86) onto the *session* stream; neither
// has ever had a session-stream handler (handleSessionMessage's 9 cases, pre-#2065, had
// neither). This predates #2065 and isn't one of its ACs — flagged to WebUI-Agent rather
// than fixed here (an actual fix needs a product decision about what should visibly happen,
// not just mechanical wiring). Tracked explicitly so it isn't lost; remove once a decision
// lands, whichever issue that turns out to be.
const PRE_EXISTING_MISSING_SESSION_HANDLER = new Set(['secret_refresh_failed', 'proxy_event'])

describe('event registry completeness (issue #2065 AC3/AC8)', () => {
  let registry
  let polling

  beforeAll(() => {
    registry = loadEventRegistryFixture()
    setActivePinia(createPinia())
    polling = usePollingStore()
  })

  it('every UI-queue-registered type has a UI handler', () => {
    const handled = new Set(polling.registeredEventTypes.ui)
    const missing = Object.entries(registry.top_level_event_types)
      .filter(([, spec]) => spec.queues.includes('ui'))
      .map(([type]) => type)
      .filter((type) => !handled.has(type) && !PENDING_2B_B_MISSING_UI_HANDLER.has(type))
    expect(missing).toEqual([])
  })

  it('every session-queue-registered type has a session handler', () => {
    const handled = new Set(polling.registeredEventTypes.session)
    const missing = Object.entries(registry.top_level_event_types)
      .filter(([, spec]) => spec.queues.includes('session'))
      .map(([type]) => type)
      .filter((type) => !handled.has(type) && !PRE_EXISTING_MISSING_SESSION_HANDLER.has(type))
    expect(missing).toEqual([])
  })

  it('every UI handler corresponds to a type registered for the UI queue', () => {
    const registered = new Set(
      Object.entries(registry.top_level_event_types)
        .filter(([, spec]) => spec.queues.includes('ui'))
        .map(([type]) => type)
    )
    const unregistered = polling.registeredEventTypes.ui
      .filter((type) => !registered.has(type) && !PENDING_2B_B_EXTRA_UI_HANDLER.has(type))
    expect(unregistered).toEqual([])
  })

  it('every session handler corresponds to a type registered for the session queue', () => {
    const registered = new Set(
      Object.entries(registry.top_level_event_types)
        .filter(([, spec]) => spec.queues.includes('session'))
        .map(([type]) => type)
    )
    const unregistered = polling.registeredEventTypes.session.filter((type) => !registered.has(type))
    expect(unregistered).toEqual([])
  })

  it('every audit-only type is explicitly acknowledged as server-only', () => {
    const auditOnly = Object.entries(registry.top_level_event_types)
      .filter(([, spec]) => spec.queues.length === 1 && spec.queues[0] === 'audit')
      .map(([type]) => type)
    expect(new Set(auditOnly)).toEqual(AUDIT_ONLY_ALLOWLIST)
  })
})
