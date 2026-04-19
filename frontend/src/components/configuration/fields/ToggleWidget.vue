<template>
  <div class="form-check form-switch mb-0">
    <input
      class="form-check-input"
      type="checkbox"
      :id="inputId"
      :checked="value"
      :disabled="disabled"
      @change="$emit('update:value', $event.target.checked)"
    />
    <label v-if="label" class="form-check-label" :for="inputId" style="text-transform: none; letter-spacing: normal;">
      {{ label }}
    </label>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  value: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  label: { type: String, default: '' },
})

defineEmits(['update:value'])

const inputId = computed(() => `toggle-${Math.random().toString(36).slice(2, 9)}`)
</script>
