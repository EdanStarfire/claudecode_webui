<template>
  <div class="budget-slider-group" :class="{ 'slider-default': isShowingDefault }">
    <input
      type="range"
      class="form-range"
      :min="min"
      :max="max"
      :step="step"
      :value="displayValue"
      :disabled="disabled"
      @input="$emit('update:value', parseInt($event.target.value))"
    />
    <span class="budget-value">{{ displayValue.toLocaleString() }}</span>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  value: { default: null },
  disabled: { type: Boolean, default: false },
  min: { type: Number, default: 0 },
  max: { type: Number, default: 100 },
  step: { type: Number, default: 1 },
  defaultValue: { type: Number, default: 0 },
})

defineEmits(['update:value'])

const isShowingDefault = computed(() =>
  (props.value === null || props.value === undefined) && props.defaultValue != null
)

const displayValue = computed(() =>
  props.value != null ? props.value : props.defaultValue
)
</script>

<style scoped>
.slider-default {
  opacity: 0.45;
}
</style>
