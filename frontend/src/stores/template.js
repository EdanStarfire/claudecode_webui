import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { api, getAuthToken } from '@/utils/api'

export const useTemplateStore = defineStore('template', () => {
  // templates Map: template_id → template object
  const templates = ref(new Map())
  const loading = ref(false)
  const error = ref(null)

  // ---- computed ----

  const templateList = computed(() => [...templates.value.values()])

  function getTemplate(templateId) {
    return templates.value.get(templateId) ?? null
  }

  // ---- CRUD actions ----

  async function fetchTemplates() {
    loading.value = true
    error.value = null
    try {
      const data = await api.get('/api/templates')
      const list = data.templates || []
      const map = new Map()
      for (const t of list) {
        map.set(t.template_id, t)
      }
      templates.value = map
    } catch (err) {
      console.error('Failed to load templates:', err)
      error.value = err.message || 'Failed to load templates'
    } finally {
      loading.value = false
    }
  }

  async function createTemplate(payload) {
    const result = await api.post('/api/templates', payload)
    await fetchTemplates()
    return result
  }

  async function updateTemplate(templateId, payload) {
    const result = await api.put(`/api/templates/${templateId}`, payload)
    await fetchTemplates()
    return result
  }

  async function deleteTemplate(templateId) {
    await api.delete(`/api/templates/${templateId}`)
    templates.value.delete(templateId)
    templates.value = new Map(templates.value)
  }

  async function importTemplate(envelope, overwrite = false) {
    const result = await api.post('/api/templates/import', { ...envelope, overwrite })
    await fetchTemplates()
    return result
  }

  async function exportTemplate(templateId) {
    const token = getAuthToken()
    const headers = token ? { Authorization: `Bearer ${token}` } : {}
    const response = await fetch(`/api/templates/${templateId}/export`, { headers })
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    const blob = await response.blob()
    const disposition = response.headers.get('Content-Disposition') || ''
    const match = disposition.match(/filename="([^"]+)"/)
    const template = templates.value.get(templateId)
    const filename = match ? match[1] : `${template?.name ?? templateId}.template.json`
    return { blob, filename }
  }

  return {
    templates,
    loading,
    error,
    templateList,
    getTemplate,
    fetchTemplates,
    createTemplate,
    updateTemplate,
    deleteTemplate,
    importTemplate,
    exportTemplate,
  }
})
