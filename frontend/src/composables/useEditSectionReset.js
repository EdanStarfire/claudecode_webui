import { useSettingsStore } from '@/stores/settings'
import { FIELD_RESET } from './fieldResetSentinel.js'

/**
 * Stages a "reset field to inherited/default" in the draft for template/profile edit sections.
 *
 * The reset is a draft operation: it sets the field to FIELD_RESET in the draft so the
 * field shows its inherited value immediately. On Save, the key is deleted from the
 * persisted config. On Cancel, the whole draft (including the reset) is discarded.
 */
export function useEditSectionReset({ areaKey }) {
  const settingsStore = useSettingsStore()

  function handleReset(key) {
    settingsStore.setField(areaKey.value, key, FIELD_RESET)
  }

  return { handleReset }
}
