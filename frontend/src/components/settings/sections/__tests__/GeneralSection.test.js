import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { renderWithStores } from '@/test-utils/render'
import GeneralSection from '@/components/settings/sections/GeneralSection.vue'
import { makeSession } from '@/test-utils/factories'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn()
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))

const ROUTES = [
  { path: '/settings/session/:sessionId/general', component: { template: '<div/>' } }
]

const AREA_KEY = 'session:__new__:general'

beforeEach(() => {
  setActivePinia(createPinia())
  Object.values(apiMock).forEach(fn => fn.mockReset())
  apiMock.get.mockImplementation((url) => {
    if (url === '/api/templates') return Promise.resolve({ templates: [] })
    if (url === '/api/profiles') return Promise.resolve({ profiles: [] })
    return Promise.resolve({})
  })
})

async function flush() {
  await new Promise(r => setTimeout(r, 0))
}

// Issue #2164: seed a new session's draft from an existing session's configuration.
async function mountSeeded(sourceOverrides = {}) {
  const url = '/settings/session/__new__/general?project_id=proj-1&seed_session_id=src-1'
  const rendered = renderWithStores(GeneralSection, { routes: ROUTES, initialRoute: url })

  // Seed the source session into the store BEFORE the awaited re-push below, so the
  // seed-read watcher (which fires reactively once route.query settles) finds it
  // synchronously instead of falling through to a real fetchSessionDetails() network call.
  const { useSessionStore } = await import('@/stores/session')
  const sessionStore = useSessionStore(rendered.pinia)
  sessionStore.sessions.set('src-1', makeSession({
    session_id: 'src-1',
    project_id: 'proj-1',
    template_id: 'tmpl-1',
    config: { model: 'opus' },
    working_directory: '/source/dir',
    ...sourceOverrides,
  }))

  // Mirrors the SecretGeneralSection race workaround: renderWithStores() fires an
  // un-awaited router.push() before mount(); re-push now and await it so
  // route.query is settled for the reactive seed-read watcher.
  await rendered.router.push(url)
  await flush()
  return rendered
}

describe('GeneralSection — seed a new session from an existing one (issue #2164)', () => {
  it('prefills the draft template_id from the source session', async () => {
    const { pinia } = await mountSeeded()
    const { useSettingsStore } = await import('@/stores/settings')
    const settingsStore = useSettingsStore(pinia)

    expect(settingsStore.getDraft(AREA_KEY).template_id).toBe('tmpl-1')
  })

  it('does not copy working_directory from the source session', async () => {
    const { pinia } = await mountSeeded()
    const { useSettingsStore } = await import('@/stores/settings')
    const settingsStore = useSettingsStore(pinia)

    expect(settingsStore.getDraft(AREA_KEY).working_directory).toBeUndefined()
  })

  it("handleSave()'s outgoing payload includes the source's CONFIG_FIELDS overrides", async () => {
    apiMock.get.mockImplementation((url) => {
      if (url === '/api/templates') return Promise.resolve({ templates: [] })
      if (url === '/api/profiles') return Promise.resolve({ profiles: [] })
      if (url === '/api/sessions/new-id') return Promise.resolve({ session: makeSession({ session_id: 'new-id' }) })
      return Promise.resolve({})
    })
    apiMock.post.mockResolvedValue({ session_id: 'new-id' })

    const { pinia, wrapper } = await mountSeeded()
    const { useSettingsStore } = await import('@/stores/settings')
    const settingsStore = useSettingsStore(pinia)
    settingsStore.setField(AREA_KEY, 'name', 'My Seeded Session')

    await wrapper.vm.save()
    await flush()

    expect(apiMock.post).toHaveBeenCalledWith('/api/sessions', expect.objectContaining({
      project_id: 'proj-1',
      name: 'My Seeded Session',
      template_id: 'tmpl-1',
      model: 'opus',
    }))
  })

  it('does not stash a seed config when seed_session_id is absent (ordinary new-session flow)', async () => {
    const url = '/settings/session/__new__/general?project_id=proj-1'
    const rendered = renderWithStores(GeneralSection, { routes: ROUTES, initialRoute: url })
    await rendered.router.push(url)
    await flush()

    const { useSettingsStore } = await import('@/stores/settings')
    const settingsStore = useSettingsStore(rendered.pinia)
    expect(settingsStore.getDraft(AREA_KEY).template_id).toBeUndefined()
  })

  it('clears a previously-seeded config when navigating to a plain __new__ route in the same component instance', async () => {
    // Regression guard: SettingsLayout.vue keys the mounted section on route.path only
    // (not the full route with query), so a seeded __new__ route and a plain __new__
    // route resolve to the SAME component instance — without an explicit reset, a stale
    // seedConfig would otherwise leak into an unrelated, un-seeded session creation.
    // Uses a template-less source so the only thing that *could* leak is seedConfig
    // itself (model), not the separate, pre-existing settingsStore draft persistence
    // that already applies to the template picker regardless of this feature.
    apiMock.get.mockImplementation((url) => {
      if (url === '/api/templates') return Promise.resolve({ templates: [] })
      if (url === '/api/profiles') return Promise.resolve({ profiles: [] })
      if (url === '/api/sessions/new-id') return Promise.resolve({ session: makeSession({ session_id: 'new-id' }) })
      return Promise.resolve({})
    })
    apiMock.post.mockResolvedValue({ session_id: 'new-id' })

    const { pinia, router, wrapper } = await mountSeeded({ template_id: null, config: { model: 'opus' } })
    const { useSettingsStore } = await import('@/stores/settings')
    const settingsStore = useSettingsStore(pinia)
    expect(settingsStore.getDraft(AREA_KEY).template_id).toBeNull() // seed applied (no template)

    // Same component instance, now navigate (within the same mounted test) to a plain
    // __new__ route for a different project, with no seed_session_id.
    await router.push('/settings/session/__new__/general?project_id=proj-2')
    await flush()

    settingsStore.setField(AREA_KEY, 'name', 'Unrelated New Session')
    await wrapper.vm.save()
    await flush()

    const [, payload] = apiMock.post.mock.calls[0]
    expect(payload.model).toBeUndefined()
  })
})

// Issue #2264: block Save while the cold-cache seed source fetch is in flight.
describe('GeneralSection — Save blocked during cold-cache seed fetch (issue #2264)', () => {
  const URL = '/settings/session/__new__/general?project_id=proj-1&seed_session_id=src-1'

  function deferredSource() {
    let resolve, reject
    const promise = new Promise((res, rej) => { resolve = res; reject = rej })
    apiMock.get.mockImplementation((url) => {
      if (url === '/api/templates') return Promise.resolve({ templates: [] })
      if (url === '/api/profiles') return Promise.resolve({ profiles: [] })
      if (url === '/api/sessions/src-1') return promise
      return Promise.resolve({})
    })
    return { resolve, reject }
  }

  async function mountCold() {
    const rendered = renderWithStores(GeneralSection, { routes: ROUTES, initialRoute: URL })
    await rendered.router.push(URL)
    await flush()
    return rendered
  }

  const saveDisabled = (wrapper) => wrapper.find('.btn-save').element.disabled

  it('disables Save while pending, enables it after resolve, and includes the seed config', async () => {
    const d = deferredSource()
    apiMock.post.mockResolvedValue({ session_id: 'new-id' })
    const { wrapper } = await mountCold()
    expect(saveDisabled(wrapper)).toBe(true)

    // handleSave is a no-op while loading
    await wrapper.vm.save()
    expect(apiMock.post).not.toHaveBeenCalled()

    d.resolve({ session: makeSession({ session_id: 'src-1', template_id: 'tmpl-1', config: { model: 'opus' } }) })
    await flush()
    expect(saveDisabled(wrapper)).toBe(false)

    await wrapper.vm.save()
    await flush()
    expect(apiMock.post).toHaveBeenCalledWith('/api/sessions', expect.objectContaining({ model: 'opus' }))
  })

  it('re-enables Save when the fetch rejects', async () => {
    const d = deferredSource()
    const { wrapper } = await mountCold()
    expect(saveDisabled(wrapper)).toBe(true)

    d.reject(new Error('404'))
    await flush()
    expect(saveDisabled(wrapper)).toBe(false)
  })

  it('re-enables Save when the fetch returns no session', async () => {
    const d = deferredSource()
    const { wrapper } = await mountCold()
    d.resolve({})
    await flush()
    expect(saveDisabled(wrapper)).toBe(false)
  })

  it('never disables Save when the source is already cached', async () => {
    const { wrapper } = await mountSeeded()
    expect(saveDisabled(wrapper)).toBe(false)
  })

  it('clears the flag when navigating to a plain __new__ route mid-fetch', async () => {
    deferredSource()
    const { wrapper, router } = await mountCold()
    expect(saveDisabled(wrapper)).toBe(true)

    await router.push('/settings/session/__new__/general?project_id=proj-2')
    await flush()
    expect(saveDisabled(wrapper)).toBe(false)
  })

  it('clears the flag when switching to a cached seed while another fetch is pending', async () => {
    deferredSource()
    const { wrapper, router, pinia } = await mountCold()
    expect(saveDisabled(wrapper)).toBe(true)

    const { useSessionStore } = await import('@/stores/session')
    useSessionStore(pinia).sessions.set('src-2', makeSession({ session_id: 'src-2', config: {} }))
    await router.push('/settings/session/__new__/general?project_id=proj-1&seed_session_id=src-2')
    await flush()
    expect(saveDisabled(wrapper)).toBe(false)
  })
})
