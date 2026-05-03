import { ref } from 'vue'
import { useSettingsStore } from '@/stores/settings'
import { useTemplateStore } from '@/stores/template'
import { useProfileStore } from '@/stores/profile'

/**
 * Handles "reset field to inherited/default" for template and profile edit sections.
 *
 * Reset removes the key from both the local draft and the persisted config.
 * After reset the field inherits from the bound profile (template mode) or falls back to default.
 */
export function useEditSectionReset({ isTemplateMode, areaKey, entityId, baseConfig }) {
  const settingsStore = useSettingsStore()
  const templateStore = useTemplateStore()
  const profileStore  = useProfileStore()
  const resetting     = ref(false)

  async function handleReset(key) {
    if (resetting.value) return
    resetting.value = true
    try {
      // Remove from draft if present, preserving other draft fields
      const currentDraft = { ...(settingsStore.getDraft(areaKey.value) || {}) }
      if (key in currentDraft) {
        delete currentDraft[key]
        settingsStore.discardDraft(areaKey.value)
        for (const [k, v] of Object.entries(currentDraft)) {
          settingsStore.setField(areaKey.value, k, v)
        }
      }

      // Remove from persisted config if present
      const currentBase = baseConfig.value || {}
      if (key in currentBase) {
        const newConfig = { ...currentBase }
        delete newConfig[key]
        if (isTemplateMode.value) {
          await templateStore.updateTemplate(entityId.value, { config: newConfig })
        } else {
          await profileStore.updateProfile(entityId.value, { config: newConfig })
        }
      }
    } catch (err) {
      console.error('Reset failed:', err)
    } finally {
      resetting.value = false
    }
  }

  return { handleReset, resetting }
}
