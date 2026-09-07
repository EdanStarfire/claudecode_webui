import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { renderWithStores } from '@/test-utils/render'
import SecretGeneralSection from '@/components/settings/sections/SecretGeneralSection.vue'
import SettingsToolbar from '@/components/settings/SettingsToolbar.vue'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn()
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))

const ROUTES = [
  { path: '/settings/secret/:secretName', component: { template: '<div/>' } }
]

const AREA_KEY = 'secret:__new__:general'

beforeEach(() => {
  setActivePinia(createPinia())
  Object.values(apiMock).forEach(fn => fn.mockReset())
  apiMock.get.mockResolvedValue({ secrets: [] })
})

async function flush() {
  await new Promise(r => setTimeout(r, 0))
}

async function mountNew(type, fields = {}) {
  const { useSettingsStore } = await import('@/stores/settings')
  const { useSecretsStore } = await import('@/stores/secrets')

  const rendered = renderWithStores(SecretGeneralSection, {
    routes: ROUTES,
    initialRoute: `/settings/secret/__new__?type=${type}`
  })
  // renderWithStores() fires an un-awaited router.push() before mount(); mount()'s
  // router.install() then re-navigates to the still-pending initial location, which
  // can race and leave currentRoute unresolved. Re-push (now that install() has run
  // and the router is started) and await it so route.params/query are settled.
  await rendered.router.push(`/settings/secret/__new__?type=${type}`)

  const settingsStore = useSettingsStore(rendered.pinia)
  const secretsStore = useSecretsStore(rendered.pinia)
  secretsStore.loaded = true // skip fetchIfEmpty network call

  await flush()

  settingsStore.setField(AREA_KEY, 'name', 'my-secret')
  settingsStore.setField(AREA_KEY, 'target_hosts_raw', 'api.example.com')
  settingsStore.setField(AREA_KEY, 'type', type)
  for (const [key, value] of Object.entries(fields)) {
    settingsStore.setField(AREA_KEY, key, value)
  }
  await flush()

  return rendered
}

async function mountEdit(existingSecret, fields = {}) {
  const { useSettingsStore } = await import('@/stores/settings')
  const { useSecretsStore } = await import('@/stores/secrets')

  const rendered = renderWithStores(SecretGeneralSection, {
    routes: ROUTES,
    initialRoute: `/settings/secret/${existingSecret.name}`
  })
  await rendered.router.push(`/settings/secret/${existingSecret.name}`)

  const settingsStore = useSettingsStore(rendered.pinia)
  const secretsStore = useSecretsStore(rendered.pinia)
  secretsStore.loaded = true
  secretsStore.secrets = [existingSecret]

  await flush()

  const areaKey = `secret:${existingSecret.name}:general`
  for (const [key, value] of Object.entries(fields)) {
    settingsStore.setField(areaKey, key, value)
  }
  await flush()

  return rendered
}

function saveDisabled(wrapper) {
  return wrapper.findComponent(SettingsToolbar).props('saveDisabled')
}

describe('SecretGeneralSection canSave — basic_auth', () => {
  it.each([
    ['username only', { username: 'alice' }, false],
    ['password only', { value: 'secret-pass' }, false],
    ['both username and password', { username: 'alice', value: 'secret-pass' }, false],
    ['neither username nor password', {}, true],
  ])('%s → save disabled = %s', async (_label, fields, expectDisabled) => {
    const { wrapper } = await mountNew('basic_auth', fields)
    expect(saveDisabled(wrapper)).toBe(expectDisabled)
  })
})

describe('SecretGeneralSection canSave — other types unaffected', () => {
  it('disables save for api_key type with no value', async () => {
    const { wrapper } = await mountNew('api_key', {})
    expect(saveDisabled(wrapper)).toBe(true)
  })
})

describe('SecretGeneralSection save payload — basic_auth', () => {
  it('sends value="" (not omitted) when creating a username-only secret', async () => {
    apiMock.post.mockResolvedValue({ name: 'my-secret', type: 'basic_auth' })
    const { wrapper } = await mountNew('basic_auth', { username: 'alice' })

    await wrapper.vm.save()
    await flush()

    expect(apiMock.post).toHaveBeenCalledWith('/api/secrets', expect.objectContaining({
      username: 'alice',
      value: '',
    }))
  })
})

describe('SecretGeneralSection save payload — oauth2', () => {
  it('sends no scrub key when refresh is configured and all scrub sub-fields are empty', async () => {
    apiMock.post.mockResolvedValue({ name: 'my-oauth', type: 'oauth2' })
    const { wrapper } = await mountNew('oauth2', {
      value: 'initial-access-token',
      refresh_token_url: 'https://example.com/token',
      refresh_client_id: 'client-id',
      refresh_token_secret_name: 'refresh-token-secret',
    })

    expect(saveDisabled(wrapper)).toBe(false)

    await wrapper.vm.save()
    await flush()

    expect(apiMock.post).toHaveBeenCalledTimes(1)
    const [, payload] = apiMock.post.mock.calls[0]
    expect(payload.scrub == null).toBe(true)
    expect(payload.refresh).toEqual(expect.objectContaining({
      token_url: 'https://example.com/token',
      client_id: 'client-id',
      refresh_token_secret_name: 'refresh-token-secret',
    }))
  })

  it('sends scrub when a scrub sub-field is filled in', async () => {
    apiMock.post.mockResolvedValue({ name: 'my-oauth', type: 'oauth2' })
    const { wrapper } = await mountNew('oauth2', {
      value: 'initial-access-token',
      refresh_token_url: 'https://example.com/token',
      refresh_client_id: 'client-id',
      refresh_token_secret_name: 'refresh-token-secret',
      scrub_matcher_regex: 'access_token=([^&]+)',
    })

    await wrapper.vm.save()
    await flush()

    const [, payload] = apiMock.post.mock.calls[0]
    expect(payload.scrub).toEqual(expect.objectContaining({
      matcher_regex: 'access_token=([^&]+)',
    }))
  })

  it('still sends a scrub object (not null) when editing and blanking scrub fields', async () => {
    // Regression guard: on PATCH, the backend treats a null scrub as "leave
    // unchanged", not "clear it" — so blanking scrub on an existing oauth2
    // secret must still send the (now-empty) scrub object, never null,
    // otherwise the edit would silently fail to clear the old scrub config.
    apiMock.patch.mockResolvedValue({ name: 'existing-oauth', type: 'oauth2' })
    const existingSecret = {
      name: 'existing-oauth',
      type: 'oauth2',
      target_hosts: ['api.example.com'],
      refresh: {
        token_url: 'https://example.com/token',
        client_id: 'client-id',
        refresh_token_secret_name: 'refresh-token-secret',
      },
      scrub: { matcher_regex: 'access_token=([^&]+)' },
    }
    const { wrapper } = await mountEdit(existingSecret, {
      scrub_url_path: '',
      scrub_matcher_jsonpath: '',
      scrub_matcher_regex: '',
    })

    await wrapper.vm.save()
    await flush()

    expect(apiMock.patch).toHaveBeenCalledTimes(1)
    const [, payload] = apiMock.patch.mock.calls[0]
    expect(payload.scrub).not.toBeNull()
    expect(payload.scrub).toEqual(expect.objectContaining({
      matcher_regex: '',
    }))
  })
})
