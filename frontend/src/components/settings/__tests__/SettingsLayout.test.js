import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { screen } from '@testing-library/vue'
import { renderWithStores } from '@/test-utils/render'
import SettingsLayout from '@/components/settings/SettingsLayout.vue'
import { useSettingsStore } from '@/stores/settings'
import { useUIStore } from '@/stores/ui'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn()
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))

// A route that matches no known settings section, so SettingsLayout renders its
// "coming soon" placeholder instead of mounting a real section component.
const SETTINGS_ROUTE = '/settings/__unmatched__'
const CONTENT_ROUTE = '/session/abc'

const ROUTES = [
  { path: '/', component: { template: '<div/>' } },
  { path: '/session/:sessionId', component: { template: '<div/>' } },
  { path: '/settings/:section*', component: { template: '<div/>' } },
]

const AREA_KEY = 'session:abc:general'

beforeEach(() => {
  setActivePinia(createPinia())
  Object.values(apiMock).forEach(fn => fn.mockReset())
})

async function flush() {
  await new Promise(r => setTimeout(r, 0))
}

async function mountSettings() {
  const rendered = renderWithStores(SettingsLayout, {
    routes: ROUTES,
    initialRoute: SETTINGS_ROUTE,
  })
  // Mirror the app's real router/index.js beforeEach guard so the dirty-navigation
  // flow (requestNavigation/pendingNavigation) behaves the same as in production.
  rendered.router.beforeEach((to, _from, next) => {
    const settingsStore = useSettingsStore(rendered.pinia)
    next(settingsStore.requestNavigation(to) ? false : true)
  })
  await rendered.router.push(SETTINGS_ROUTE)
  await flush()

  const settingsStore = useSettingsStore(rendered.pinia)
  const uiStore = useUIStore(rendered.pinia)
  uiStore.setLastContentRoute(CONTENT_ROUTE)

  return { ...rendered, settingsStore, uiStore }
}

function pressEscape() {
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
}

describe('SettingsLayout Escape handling', () => {
  it('navigates out of Settings immediately when there are no unsaved changes', async () => {
    const { router } = await mountSettings()

    pressEscape()
    await flush()

    expect(router.currentRoute.value.path).toBe(CONTENT_ROUTE)
  })

  it('shows the DirtyGuardModal instead of navigating when a section is dirty', async () => {
    const { router, settingsStore } = await mountSettings()
    settingsStore.setField(AREA_KEY, 'name', 'renamed')

    pressEscape()
    await flush()

    expect(router.currentRoute.value.path).toBe(SETTINGS_ROUTE)
    expect(settingsStore.pendingNavigation).not.toBeNull()
    expect(screen.getByRole('dialog')).toBeTruthy()
  })

  it('Discard clears every dirty area and completes the navigation', async () => {
    const { router, settingsStore } = await mountSettings()
    settingsStore.setField(AREA_KEY, 'name', 'renamed')
    settingsStore.setField('session:abc:model-tuning', 'temperature', 0.5)

    pressEscape()
    await flush()

    screen.getByText('Discard').click()
    await flush()

    expect(settingsStore.dirtyAreas.size).toBe(0)
    expect(settingsStore.pendingNavigation).toBeNull()
    expect(router.currentRoute.value.path).toBe(CONTENT_ROUTE)
  })

  it('Cancel keeps the user in Settings with drafts intact', async () => {
    const { router, settingsStore } = await mountSettings()
    settingsStore.setField(AREA_KEY, 'name', 'renamed')

    pressEscape()
    await flush()

    screen.getByText('Cancel').click()
    await flush()

    expect(settingsStore.pendingNavigation).toBeNull()
    expect(settingsStore.dirtyAreas.has(AREA_KEY)).toBe(true)
    expect(router.currentRoute.value.path).toBe(SETTINGS_ROUTE)
  })

  it('does not intercept Escape when a foreign modal is open on top of Settings', async () => {
    const { router, uiStore, settingsStore } = await mountSettings()
    uiStore.showModal('folder-browser')
    await flush()

    pressEscape()
    await flush()

    expect(router.currentRoute.value.path).toBe(SETTINGS_ROUTE)
    expect(settingsStore.pendingNavigation).toBeNull()
  })

  it('a second Escape while the guard is open cancels the guard instead of re-navigating', async () => {
    const { router, settingsStore } = await mountSettings()
    settingsStore.setField(AREA_KEY, 'name', 'renamed')

    pressEscape()
    await flush()
    expect(settingsStore.pendingNavigation).not.toBeNull()

    pressEscape()
    await flush()

    expect(settingsStore.pendingNavigation).toBeNull()
    expect(settingsStore.dirtyAreas.has(AREA_KEY)).toBe(true)
    expect(router.currentRoute.value.path).toBe(SETTINGS_ROUTE)
  })
})
