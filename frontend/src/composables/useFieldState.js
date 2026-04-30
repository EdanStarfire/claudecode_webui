/**
 * useFieldState — per-field resolution metadata for the 3-tier config chain.
 *
 * 3-tier priority (high → low):
 *   session.config > template.config > profile.config > field defaults
 *
 * Returns a reactive map of fieldName → { effective_value, source, source_label, is_set_here }.
 * "is_set_here" means the field is explicitly set in the current editing layer (session or template).
 *
 * @param {Object} options
 * @param {import('vue').Ref} options.sessionConfig  - session.config dict (or {})
 * @param {import('vue').Ref} options.templateConfig - template.config dict (or null/undefined)
 * @param {import('vue').Ref} options.profileConfig  - merged profile config (or null/undefined)
 * @param {'session'|'template'} options.layer       - which layer is being edited
 */
import { computed } from 'vue'
import { CONFIG_FIELDS_LIST, FIELD_DEFAULTS } from '@/utils/configFields'

export function useFieldState({ sessionConfig, templateConfig, profileConfig, layer }) {
  const fieldStates = computed(() => {
    const sc = sessionConfig?.value ?? {}
    const tc = templateConfig?.value ?? {}
    const pc = profileConfig?.value ?? {}
    const result = {}

    for (const field of CONFIG_FIELDS_LIST) {
      const inSession = Object.prototype.hasOwnProperty.call(sc, field)
      const inTemplate = Object.prototype.hasOwnProperty.call(tc, field)
      const inProfile = Object.prototype.hasOwnProperty.call(pc, field)

      let source, sourceLabel, effectiveValue

      if (inSession) {
        source = 'session'
        sourceLabel = 'Session'
        effectiveValue = sc[field]
      } else if (inTemplate) {
        source = 'template'
        sourceLabel = 'Template'
        effectiveValue = tc[field]
      } else if (inProfile) {
        source = 'profile'
        sourceLabel = 'Profile'
        effectiveValue = pc[field]
      } else {
        source = 'default'
        sourceLabel = 'Default'
        effectiveValue = FIELD_DEFAULTS[field] ?? null
      }

      const currentLayerValue = layer === 'session' ? sc[field] : tc[field]
      const isSetHere = layer === 'session' ? inSession : inTemplate

      result[field] = {
        effective_value: effectiveValue,
        source,
        source_label: sourceLabel,
        is_set_here: isSetHere,
        current_layer_value: currentLayerValue,
      }
    }

    return result
  })

  return { fieldStates }
}
