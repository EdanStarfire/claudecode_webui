import { computed } from 'vue'
import { FIELD_RESET } from './fieldResetSentinel.js'

function hasValue(v) {
  if (v === null || v === undefined || v === '') return false
  if (Array.isArray(v)) return v.length > 0
  return true
}

/**
 * Computes per-field source states ({ kind: 'S'|'T'|'P'|'EMPTY' }) for template/profile/session
 * edit section components, to drive SourceMarker badges in FieldRenderer.
 *
 * In session mode (isSessionMode):
 *   S = value is set on the session itself (or in the current draft)
 *   T = value is unset on session but the bound template has it
 *   P = value is unset on session and template, but the bound profile has it
 *   EMPTY = none of the above
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
export function useEditSectionFieldStates({ isTemplateMode, isSessionMode, isScheduleMode, baseConfig, draft, boundProfile, boundTemplate, schemaFields }) {
  const fieldStates = computed(() => {
    const states = {}
    const fields = typeof schemaFields === 'function' ? schemaFields() : (schemaFields?.value ?? schemaFields ?? [])
    // "set at this level" kind: S for session/schedule, T for template, P for profile
    const selfKind = (isSessionMode?.value || isScheduleMode?.value) ? 'S' : (isTemplateMode.value ? 'T' : 'P')

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

      // Session mode: check bound template config for 'T' source
      if (isSessionMode?.value && boundTemplate?.value) {
        const templateVal = boundTemplate.value?.config?.[key]
        if (hasValue(templateVal)) {
          states[key] = { kind: 'T', templateName: boundTemplate.value?.name || 'Template' }
          continue
        }
      }

      // Template mode or session mode: check bound profile config for 'P' source
      if ((isTemplateMode.value || isSessionMode?.value) && boundProfile.value) {
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
