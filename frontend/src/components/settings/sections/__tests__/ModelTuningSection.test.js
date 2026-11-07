import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { renderWithStores } from '@/test-utils/render'
import ModelTuningSection from '@/components/settings/sections/ModelTuningSection.vue'
import RestartRequiredBanner from '@/components/settings/RestartRequiredBanner.vue'
import { makeSession } from '@/test-utils/factories'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn()
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))

const ROUTES = [
  { path: '/settings/session/:sessionId/:section', component: { template: '<div/>' } }
]
const URL = '/settings/session/sess-1/model-tuning'
const AREA_KEY = 'session:sess-1:model-tuning'

beforeEach(() => {
  setActivePinia(createPinia())
  Object.values(apiMock).forEach(fn => fn.mockReset())
  apiMock.get.mockImplementation((url) => {
    if (url === '/api/templates') return Promise.resolve({ templates: [] })
    if (url === '/api/profiles') return Promise.resolve({ profiles: [] })
    return Promise.resolve({})
  })
})

const flush = () => new Promise(r => setTimeout(r, 0))

async function mountSection(component, sessionOverrides = {}) {
  const rendered = renderWithStores(component, { routes: ROUTES, initialRoute: URL })
  const { useSessionStore } = await import('@/stores/session')
  const { useSettingsStore } = await import('@/stores/settings')
  const sessionStore = useSessionStore(rendered.pinia)
  sessionStore.sessions.set('sess-1', makeSession({
    session_id: 'sess-1',
    state: 'active',
    config: { provider_catalog_id: 'catA', provider_model_id: 'm1' },
    current_model: 'catA--m1',
    ...sessionOverrides,
  }))
  await rendered.router.push(URL)
  await flush()
  return { ...rendered, sessionStore, settingsStore: useSettingsStore(rendered.pinia) }
}

describe('ModelTuningSection — Provider Model save (issue #2333)', () => {
  it('active session: live-switches via the provider-model endpoint, then persists the config', async () => {
    apiMock.post.mockResolvedValue({ success: true, model: 'catB--m1', applied: true, restart_required: [] })
    apiMock.patch.mockResolvedValue({ session: { config: { provider_catalog_id: 'catB', provider_model_id: 'm1' } } })
    const { wrapper, settingsStore } = await mountSection(ModelTuningSection)
    settingsStore.setField(AREA_KEY, 'provider_catalog_id', 'catB')
    settingsStore.setField(AREA_KEY, 'provider_model_id', 'm1')

    await wrapper.vm.save()
    await flush()

    expect(apiMock.post).toHaveBeenCalledWith('/api/sessions/sess-1/provider-model', {
      provider_catalog_id: 'catB', provider_model_id: 'm1'
    })
    expect(apiMock.patch).toHaveBeenCalledWith('/api/sessions/sess-1', {
      config: expect.objectContaining({ provider_catalog_id: 'catB', provider_model_id: 'm1' })
    })
    // live switch happens before the config PATCH
    expect(apiMock.post.mock.invocationCallOrder[0]).toBeLessThan(apiMock.patch.mock.invocationCallOrder[0])
    expect(wrapper.find('[data-testid="model-save-error"]').exists()).toBe(false)
    expect(settingsStore.dirtyAreas.has(AREA_KEY)).toBe(false)
  })

  it('stopped session: only the config PATCH, no live switch (AC8)', async () => {
    apiMock.patch.mockResolvedValue({ session: {} })
    const { wrapper, settingsStore } = await mountSection(ModelTuningSection, { state: 'created' })
    settingsStore.setField(AREA_KEY, 'provider_model_id', 'm2')

    await wrapper.vm.save()
    await flush()

    expect(apiMock.post).not.toHaveBeenCalled()
    expect(apiMock.patch).toHaveBeenCalledTimes(1)
  })

  it('failed live switch: inline error, nothing persisted, draft stays dirty', async () => {
    apiMock.post.mockRejectedValue(Object.assign(new Error('HTTP 400'), { data: { detail: 'upstream rejected catB--m1' } }))
    const { wrapper, settingsStore, sessionStore } = await mountSection(ModelTuningSection)
    settingsStore.setField(AREA_KEY, 'provider_catalog_id', 'catB')
    settingsStore.setField(AREA_KEY, 'provider_model_id', 'm1')

    await wrapper.vm.save()
    await flush()

    const alert = wrapper.find('[data-testid="model-save-error"]')
    expect(alert.exists()).toBe(true)
    expect(alert.text()).toContain('upstream rejected catB--m1')
    expect(apiMock.patch).not.toHaveBeenCalled()
    expect(settingsStore.dirtyAreas.has(AREA_KEY)).toBe(true)
    expect(sessionStore.getSession('sess-1').config.provider_catalog_id).toBe('catA')

    wrapper.vm.cancel()
    await flush()
    expect(wrapper.find('[data-testid="model-save-error"]').exists()).toBe(false)
  })

  it('paused (permission pending) session is live-switched like an active one', async () => {
    apiMock.post.mockResolvedValue({ success: true, model: 'catA--m2', applied: true, restart_required: [] })
    apiMock.patch.mockResolvedValue({ session: {} })
    const { wrapper, settingsStore } = await mountSection(ModelTuningSection, { state: 'paused' })
    settingsStore.setField(AREA_KEY, 'provider_model_id', 'm2')
    await wrapper.vm.save()
    await flush()
    expect(apiMock.post).toHaveBeenCalledWith('/api/sessions/sess-1/provider-model', {
      provider_catalog_id: 'catA', provider_model_id: 'm2'
    })
  })

  it('a draft equal to the current provider pair does not call the live endpoint', async () => {
    apiMock.patch.mockResolvedValue({ session: {} })
    const { wrapper, settingsStore } = await mountSection(ModelTuningSection)
    settingsStore.setField(AREA_KEY, 'provider_model_id', 'm1') // same as current
    settingsStore.setField(AREA_KEY, 'thinking_mode', 'adaptive')
    await wrapper.vm.save()
    await flush()
    expect(apiMock.post).not.toHaveBeenCalled()
    expect(apiMock.patch).toHaveBeenCalledTimes(1)
  })

  it('per-tier routing enabled skips the live switch (launch-bound, backend reports the restart)', async () => {
    apiMock.patch.mockResolvedValue({ session: {} })
    const tier = {
      provider_haiku_catalog_id: 'catA', provider_haiku_model_id: 'm1',
      provider_sonnet_catalog_id: 'catA', provider_sonnet_model_id: 'm1',
      provider_opus_catalog_id: 'catA', provider_opus_model_id: 'm1',
      provider_default_tier: 'sonnet',
    }
    const { wrapper, settingsStore } = await mountSection(ModelTuningSection, {
      config: { provider_catalog_id: 'catA', provider_model_id: 'm1', docker_enabled: true, ...tier },
    })
    settingsStore.setField(AREA_KEY, 'provider_opus_model_id', 'm2')
    await wrapper.vm.save()
    await flush()
    expect(apiMock.post).not.toHaveBeenCalled()
    expect(apiMock.patch).toHaveBeenCalledTimes(1)
  })

  it('config PATCH failing after a successful live switch shows an error instead of failing silently', async () => {
    apiMock.post.mockResolvedValue({ success: true, model: 'catA--m2', applied: true, restart_required: [] })
    apiMock.patch.mockRejectedValue(Object.assign(new Error('HTTP 500'), { data: { detail: 'disk full' } }))
    const { wrapper, settingsStore } = await mountSection(ModelTuningSection)
    settingsStore.setField(AREA_KEY, 'provider_model_id', 'm2')
    await wrapper.vm.save()
    await flush()
    expect(wrapper.find('[data-testid="model-save-error"]').text()).toContain('disk full')
    expect(settingsStore.dirtyAreas.has(AREA_KEY)).toBe(true)
  })

  it('a plain Model change on a direct session uses /model and never the provider endpoint', async () => {
    apiMock.post.mockResolvedValue({ success: true })
    apiMock.patch.mockResolvedValue({ session: {} })
    const { wrapper, settingsStore } = await mountSection(ModelTuningSection, {
      config: { model: 'sonnet' }, // direct session, no provider
    })
    settingsStore.setField(AREA_KEY, 'model', 'opus')

    await wrapper.vm.save()
    await flush()

    // #1673 path unchanged: /model, never /provider-model
    expect(apiMock.post).toHaveBeenCalledWith('/api/sessions/sess-1/model', { model: 'opus' })
    expect(apiMock.post.mock.calls.some(([url]) => url.endsWith('/provider-model'))).toBe(false)
  })

  it('shows the restart chip and reasons only when the session reports restart_required', async () => {
    const { wrapper, sessionStore } = await mountSection(ModelTuningSection)
    expect(wrapper.find('[data-testid="restart-required-chip"]').exists()).toBe(false)

    sessionStore.updateSession('sess-1', { restart_required: ['Docker isolation changed: restart the session to apply.'] })
    await flush()

    const chip = wrapper.find('[data-testid="restart-required-chip"]')
    expect(chip.exists()).toBe(true)
    expect(chip.attributes('title')).toContain('Docker isolation changed')
  })
})

describe('RestartRequiredBanner (issue #2333)', () => {
  it('renders nothing without reasons, lists reasons and opens the manage modal otherwise', async () => {
    const { wrapper, sessionStore, pinia } = await mountSection(RestartRequiredBanner)
    expect(wrapper.find('[data-testid="restart-required-banner"]').exists()).toBe(false)

    sessionStore.updateSession('sess-1', { restart_required: ['Catalog change pending: restart the LiteLLM proxy, then restart the session.'] })
    await flush()

    const banner = wrapper.find('[data-testid="restart-required-banner"]')
    expect(banner.exists()).toBe(true)
    expect(banner.text()).toContain('Catalog change pending')

    const { useUIStore } = await import('@/stores/ui')
    const uiStore = useUIStore(pinia)
    await banner.find('button').trigger('click')
    await flush()
    expect(uiStore.activeModal).toBe('manage-session')
    expect(uiStore.modalData.session.session_id).toBe('sess-1')
  })
})
