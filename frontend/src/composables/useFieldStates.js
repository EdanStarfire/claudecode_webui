import { computed } from 'vue'
import { resolveFieldSource } from '@/utils/sourceCascade'

/**
 * Composable that computes source-marker state for every field in a section.
 *
 * @param {import('vue').Ref<Object>} sessionValuesRef - Reactive map of fieldKey → explicitly-set value on this object
 * @param {import('vue').Ref<Object>} templateValuesRef - Reactive map of fieldKey → value from bound template
 * @param {import('vue').Ref<string|null>} templateNameRef - Display name of bound template (null = none)
 * @param {import('vue').Ref<Object>} profileValuesRef - Reactive map of fieldKey → value from bound profile
 * @param {import('vue').Ref<string|null>} profileNameRef - Display name of bound profile (null = none)
 * @returns {{ resolveSource: (fieldKey: string) => { kind: 'S'|'T'|'P'|'EMPTY', templateName?: string, profileName?: string } }}
 */
export function useFieldStates(
  sessionValuesRef,
  templateValuesRef,
  templateNameRef,
  profileValuesRef,
  profileNameRef,
) {
  function resolveSource(fieldKey) {
    return resolveFieldSource(fieldKey, {
      sessionValue: sessionValuesRef.value?.[fieldKey] ?? null,
      templateValue: templateValuesRef.value?.[fieldKey] ?? null,
      templateName: templateNameRef.value ?? null,
      profileValue: profileValuesRef.value?.[fieldKey] ?? null,
      profileName: profileNameRef.value ?? null,
    })
  }

  // Convenience computed: returns a function — callers can also use it reactively
  // by wrapping in computed() at the call site.
  const fieldSourceResolver = computed(() => resolveSource)

  return { resolveSource, fieldSourceResolver }
}
