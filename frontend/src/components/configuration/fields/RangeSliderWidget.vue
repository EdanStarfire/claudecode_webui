<template>
  <div class="budget-slider-group">
    <input
      type="range"
      class="form-range"
      :min="min"
      :max="max"
      :step="step"
      :value="value || defaultValue"
      :disabled="disabled"
      @input="$emit('update:value', parseInt($event.target.value))"
    />
    <span class="budget-value">{{ (value || defaultValue).toLocaleString() }}</span>
  </div>
</template>

<script setup>
defineProps({
  value: { type: Number, default: null },
  disabled: { type: Boolean, default: false },
  min: { type: Number, default: 0 },
  max: { type: Number, default: 100 },
  step: { type: Number, default: 1 },
  defaultValue: { type: Number, default: 0 },
})

defineEmits(['update:value'])
</script>
