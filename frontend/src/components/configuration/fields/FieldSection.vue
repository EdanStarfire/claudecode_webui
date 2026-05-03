<template>
  <div class="field-grid">
    <template v-for="field in visibleFields" :key="field.key">
      <div :class="isNarrowWidget(field) ? 'field-cell' : 'field-cell field-cell--wide'">
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
        />
      </div>
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

defineEmits(['update:config', 'update:included', 'browse', 'reset'])

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

// Toggles can share a column; everything else needs full row width
function isNarrowWidget(field) {
  return field.widget === 'toggle'
}
</script>

<style scoped>
.field-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px 24px;
  container-type: inline-size;
}

.field-cell--wide {
  grid-column: 1 / -1;
}

/* Single column on narrow content areas */
@container (max-width: 480px) {
  .field-grid {
    grid-template-columns: 1fr;
  }

  .field-cell {
    grid-column: 1 / -1;
  }
}
</style>
