import { computed } from 'vue'
import { FIELD_RESET } from './fieldResetSentinel.js'

function hasValue(v) {
  if (v === null || v === undefined || v === '') return false
  if (Array.isArray(v)) return v.length > 0
  return true
}

/**
 * Computes per-field source states ({ kind: 'T'|'P'|'EMPTY' }) for template/profile
 * edit section components, to drive SourceMarker badges in FieldRenderer.
 *
 * In template mode:
 *   T = value is set on the template itself (or in the current draft)
 *   P = value is unset on the template but the bound profile for this area has it
 *   EMPTY = neither template nor bound profile has a value
 *
 * In profile mode:
 *   P = value is set in the profile's config (or in the current draft)
 *   EMPTY = not set
 */
export function useEditSectionFieldStates({ isTemplateMode, baseConfig, draft, boundProfile, schemaFields }) {
  const fieldStates = computed(() => {
    const states = {}
    const fields = typeof schemaFields === 'function' ? schemaFields() : (schemaFields?.value ?? schemaFields ?? [])
    // "set at this level" kind: T when editing a template, P when editing a profile
    const selfKind = isTemplateMode.value ? 'T' : 'P'

    for (const f of fields) {
      const key = f.key
      const inDraft = !!(draft.value && key in draft.value)
      const draftIsReset = inDraft && draft.value[key] === FIELD_RESET

      // Non-reset draft value → selfKind (resettable)
      if (inDraft && !draftIsReset) {
        states[key] = { kind: selfKind, resettable: true }
        continue
      }

      // Saved base config — skipped when draft marks this field as reset,
      // because the reset intent is to remove it from config on save.
      if (!draftIsReset) {
        const base = baseConfig.value || {}
        if (key in base && base[key] !== null && base[key] !== undefined) {
          states[key] = { kind: selfKind, resettable: true }
          continue
        }
      }

      if (isTemplateMode.value && boundProfile.value) {
        const profileVal = boundProfile.value?.config?.[key] ?? boundProfile.value?.[key]
        if (hasValue(profileVal)) {
          states[key] = { kind: 'P', profileName: boundProfile.value?.name || 'Profile' }
          continue
        }
      }

      states[key] = { kind: 'EMPTY' }
    }
    return states
  })

  return { fieldStates }
}
