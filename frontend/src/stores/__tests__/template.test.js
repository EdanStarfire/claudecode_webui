import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
  patch: vi.fn(),
}))

vi.mock('@/utils/api', () => ({
  api: apiMock,
  getAuthToken: vi.fn(() => null),
}))

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

const sampleTemplate = {
  template_id: 'tmpl-1',
  name: 'Code Expert',
  description: 'Expert coding template',
  model: 'claude-opus',
}

describe('template store', () => {
  async function getStore() {
    const { useTemplateStore } = await import('@/stores/template')
    return useTemplateStore()
  }

  it('starts with empty templates map', async () => {
    const store = await getStore()
    expect(store.templates.size).toBe(0)
    expect(store.templateList).toEqual([])
  })

  it('fetchTemplates populates templates map', async () => {
    apiMock.get.mockResolvedValueOnce({ templates: [sampleTemplate] })
    const store = await getStore()
    await store.fetchTemplates()
    expect(store.templates.size).toBe(1)
    expect(store.templates.get('tmpl-1')).toEqual(sampleTemplate)
    expect(store.templateList).toHaveLength(1)
    expect(store.templateList[0].name).toBe('Code Expert')
  })

  it('fetchTemplates handles empty response', async () => {
    apiMock.get.mockResolvedValueOnce({})
    const store = await getStore()
    await store.fetchTemplates()
    expect(store.templates.size).toBe(0)
  })

  it('fetchTemplates sets error on failure', async () => {
    apiMock.get.mockRejectedValueOnce(new Error('Network error'))
    const store = await getStore()
    await store.fetchTemplates()
    expect(store.error).toBe('Network error')
    expect(store.templates.size).toBe(0)
  })

  it('getTemplate returns template by id or null', async () => {
    apiMock.get.mockResolvedValueOnce({ templates: [sampleTemplate] })
    const store = await getStore()
    await store.fetchTemplates()
    expect(store.getTemplate('tmpl-1')).toEqual(sampleTemplate)
    expect(store.getTemplate('nonexistent')).toBeNull()
  })

  it('createTemplate posts payload and refreshes', async () => {
    apiMock.post.mockResolvedValueOnce({ template_id: 'tmpl-2' })
    apiMock.get.mockResolvedValueOnce({ templates: [sampleTemplate, { template_id: 'tmpl-2', name: 'New' }] })
    const store = await getStore()
    const result = await store.createTemplate({ name: 'New', model: 'claude-3' })
    expect(apiMock.post).toHaveBeenCalledWith('/api/templates', { name: 'New', model: 'claude-3' })
    expect(result).toEqual({ template_id: 'tmpl-2' })
    expect(store.templates.size).toBe(2)
  })

  it('updateTemplate puts payload and refreshes', async () => {
    apiMock.put.mockResolvedValueOnce({})
    apiMock.get.mockResolvedValueOnce({ templates: [{ ...sampleTemplate, name: 'Updated' }] })
    const store = await getStore()
    await store.updateTemplate('tmpl-1', { name: 'Updated' })
    expect(apiMock.put).toHaveBeenCalledWith('/api/templates/tmpl-1', { name: 'Updated' })
    expect(store.getTemplate('tmpl-1').name).toBe('Updated')
  })

  it('deleteTemplate removes template from map', async () => {
    apiMock.get.mockResolvedValueOnce({ templates: [sampleTemplate] })
    apiMock.delete.mockResolvedValueOnce({})
    const store = await getStore()
    await store.fetchTemplates()
    await store.deleteTemplate('tmpl-1')
    expect(apiMock.delete).toHaveBeenCalledWith('/api/templates/tmpl-1')
    expect(store.templates.has('tmpl-1')).toBe(false)
  })

  it('importTemplate posts with overwrite flag and refreshes', async () => {
    const envelope = { template: { name: 'Imported', model: 'x' } }
    apiMock.post.mockResolvedValueOnce({ template_id: 'tmpl-3' })
    apiMock.get.mockResolvedValueOnce({ templates: [{ template_id: 'tmpl-3', name: 'Imported' }] })
    const store = await getStore()
    await store.importTemplate(envelope, true)
    expect(apiMock.post).toHaveBeenCalledWith('/api/templates/import', { ...envelope, overwrite: true })
    expect(store.templates.size).toBe(1)
  })
})
