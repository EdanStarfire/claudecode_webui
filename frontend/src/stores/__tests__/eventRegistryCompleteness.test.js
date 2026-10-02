import { describe, it, expect, beforeAll } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { usePollingStore } from '../polling'
import { loadEventRegistryFixture } from './helpers/eventRegistryFixture'

// QUEUE_AUDIT-only types are a wake signal, not browser poll data (shared/event_registry.py's
// own module docstring / #2063's AC's "edge cases" note) — never expected to have a browser
// handler, on either stream.
const AUDIT_ONLY_ALLOWLIST = new Set(['audit_event', 'audit_event_flush'])

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
      .filter((type) => !handled.has(type))
    expect(missing).toEqual([])
  })

  it('every session-queue-registered type has a session handler', () => {
    const handled = new Set(polling.registeredEventTypes.session)
    const missing = Object.entries(registry.top_level_event_types)
      .filter(([, spec]) => spec.queues.includes('session'))
      .map(([type]) => type)
      .filter((type) => !handled.has(type))
    expect(missing).toEqual([])
  })

  it('every UI handler corresponds to a type registered for the UI queue', () => {
    const registered = new Set(
      Object.entries(registry.top_level_event_types)
        .filter(([, spec]) => spec.queues.includes('ui'))
        .map(([type]) => type)
    )
    const unregistered = polling.registeredEventTypes.ui
      .filter((type) => !registered.has(type))
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
