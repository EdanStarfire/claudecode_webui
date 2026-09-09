<template>
  <div>
    <template v-for="field in visibleFields" :key="field.key">
      <div v-if="field.sectionHeader" class="field-section-header">{{ field.sectionHeader }}</div>
      <FieldRenderer
        :field="field"
        :value="config[field.key]"
        :disabled="disabled"
        :show-badges="showBadges"
        :show-include-toggle="showIncludeToggle"
        :field-state="fieldStates[field.key] || 'normal'"
        :included="included[field.key] !== false"
        :config="config"
        @update:value="$emit('update:config', field.key, $event)"
        @update:included="$emit('update:included', field.key, $event)"
        @browse="$emit('browse', field.key)"
        @reset="$emit('reset', field.key)"
        @update:linked="$emit('update:linked', $event)"
      />
    </template>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import FieldRenderer from './FieldRenderer.vue'

const props = defineProps({
  fields: { type: Array, required: true },
  config: { type: Object, default: () => ({}) },
  disabled: { type: Boolean, default: false },
  showBadges: { type: Boolean, default: false },
  showIncludeToggle: { type: Boolean, default: false },
  fieldStates: { type: Object, default: () => ({}) },
  included: { type: Object, default: () => ({}) },
})

defineEmits(['update:config', 'update:included', 'browse', 'reset', 'update:linked'])

const visibleFields = computed(() => {
  return props.fields.filter((field) => {
    // Skip profileOnly fields when not in profile-manager context
    if (field.profileOnly && !props.showIncludeToggle) return false
    // Skip advancedOnly fields when in profile-manager context
    if (field.advancedOnly && props.showIncludeToggle) return false
    // Evaluate showWhen
    if (field.showWhen && !field.showWhen(props.config)) return false
    return true
  })
})
</script>

<style scoped>
.field-section-header {
  font-size: 12px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--bs-secondary-color);
  padding-top: 16px;
  margin-top: 6px;
  border-top: 1px dashed var(--bs-border-color);
}
</style>
