import { computed } from 'vue'

/**
 * Shared composable for tool result computed properties.
 * Eliminates duplicated hasResult/isError/resultContent/formattedInput
 * across 12-18 tool handlers.
 *
 * @param {import('vue').Ref<Object>} toolCallRef - reactive ref or toRef to the toolCall prop
 * @returns {{ hasResult: import('vue').ComputedRef<boolean>, isError: import('vue').ComputedRef<boolean>, resultContent: import('vue').ComputedRef<string>, formattedInput: import('vue').ComputedRef<string> }}
 */
export function useToolResult(toolCallRef) {
  const hasResult = computed(() => {
    return toolCallRef.value?.result !== null && toolCallRef.value?.result !== undefined
  })

  const isError = computed(() => {
    return toolCallRef.value?.result?.error || toolCallRef.value?.status === 'error'
  })

  const resultContent = computed(() => {
    if (!hasResult.value) return ''

    const result = toolCallRef.value.result

    // If result has content property, use that
    if (result.content !== undefined) {
      return typeof result.content === 'string'
        ? result.content
        : JSON.stringify(result.content, null, 2)
    }

    // If result has message property (error case), use that
    if (result.message) {
      return result.message
    }

    // Otherwise stringify the whole result
    return JSON.stringify(result, null, 2)
  })

  const formattedInput = computed(() => {
    if (!toolCallRef.value?.input) return '{}'
    return JSON.stringify(toolCallRef.value.input, null, 2)
  })

  return { hasResult, isError, resultContent, formattedInput }
}
