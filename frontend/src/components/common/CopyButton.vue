<template>
  <button
    class="copy-btn"
    @click.stop="$emit('click')"
    :title="copied ? copiedLabel : (title || 'Copy to clipboard')"
    :disabled="disabled"
  >
    <svg v-if="copied" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <polyline points="20 6 9 17 4 12"></polyline>
    </svg>
    <svg v-else width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
    </svg>
    <span class="copy-label">{{ copied ? copiedLabel : label }}</span>
  </button>
</template>

<script setup>
defineProps({
  copied: {
    type: Boolean,
    default: false
  },
  disabled: {
    type: Boolean,
    default: false
  },
  label: {
    type: String,
    default: 'Copy'
  },
  copiedLabel: {
    type: String,
    default: 'Copied!'
  },
  title: {
    type: String,
    default: null
  }
})

defineEmits(['click'])
</script>
