<template>
  <div class="field-row">
    <!-- Left column: include toggle (profile-manager context) -->
    <div v-if="showIncludeToggle" class="field-toggle">
      <div class="form-check form-switch mb-0">
        <input
          class="form-check-input"
          type="checkbox"
          :checked="included"
          @change="$emit('update:included', $event.target.checked)"
        />
      </div>
    </div>

    <!-- Right column: label + widget -->
    <div
      class="field-body"
      :class="{ 'field-disabled': showIncludeToggle && !included }"
    >
      <label class="form-label small mb-1">
        {{ field.label }}
        <template v-if="showBadges && fieldState">
          <span v-if="fieldState === 'profile'" class="field-indicator profile" title="Value from profile">P</span>
          <span v-if="fieldState === 'autofilled'" class="field-indicator autofilled">&lt;</span>
          <span v-if="fieldState === 'modified'" class="field-indicator modified">*</span>
        </template>
      </label>

      <!-- Widget dispatch -->
      <ButtonGroupWidget
        v-if="field.widget === 'button-group'"
        :value="value"
        :disabled="isDisabled"
        :options="field.options || []"
        :multiple="field.multiple || false"
        @update:value="$emit('update:value', $event)"
      />

      <TagInputWidget
        v-else-if="field.widget === 'tag-input'"
        :value="value"
        :disabled="isDisabled"
        :variant="field.variant || 'allowed'"
        :quick-add-items="field.quickAddItems || null"
        :placeholder="field.placeholder || 'Add...'"
        @update:value="$emit('update:value', $event)"
      />

      <ToggleWidget
        v-else-if="field.widget === 'toggle'"
        :value="value"
        :disabled="isDisabled"
        @update:value="$emit('update:value', $event)"
      />

      <TextInputWidget
        v-else-if="field.widget === 'text-input'"
        :value="value"
        :disabled="isDisabled"
        :placeholder="field.placeholder || ''"
        :monospace="field.monospace || false"
        @update:value="$emit('update:value', $event)"
      />

      <TextareaWidget
        v-else-if="field.widget === 'textarea'"
        :value="value"
        :disabled="isDisabled"
        :placeholder="field.placeholder || ''"
        :rows="field.rows || 3"
        @update:value="$emit('update:value', $event)"
      />

      <RangeSliderWidget
        v-else-if="field.widget === 'range-slider'"
        :value="value"
        :disabled="isDisabled"
        :min="field.min || 0"
        :max="field.max || 100"
        :step="field.step || 1"
        :default-value="field.defaultValue || 0"
        @update:value="$emit('update:value', $event)"
      />

      <DirListWidget
        v-else-if="field.widget === 'dir-list'"
        :value="value"
        :disabled="isDisabled"
        :placeholder="field.placeholder || ''"
        :show-browse="field.showBrowse || false"
        @update:value="$emit('update:value', $event)"
        @browse="$emit('browse')"
      />

      <SandboxSubSectionWidget
        v-else-if="field.widget === 'sandbox-sub-section'"
        :value="value"
        :disabled="isDisabled"
        @update:value="$emit('update:value', $event)"
      />

      <small v-if="field.description" class="form-text text-muted d-block">
        {{ field.description }}
      </small>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import ButtonGroupWidget from './ButtonGroupWidget.vue'
import TagInputWidget from './TagInputWidget.vue'
import ToggleWidget from './ToggleWidget.vue'
import TextInputWidget from './TextInputWidget.vue'
import TextareaWidget from './TextareaWidget.vue'
import RangeSliderWidget from './RangeSliderWidget.vue'
import DirListWidget from './DirListWidget.vue'
import SandboxSubSectionWidget from './SandboxSubSectionWidget.vue'

const props = defineProps({
  field: { type: Object, required: true },
  value: { type: [String, Number, Boolean, Array, Object], default: undefined },
  disabled: { type: Boolean, default: false },
  showBadges: { type: Boolean, default: false },
  showIncludeToggle: { type: Boolean, default: false },
  fieldState: { type: String, default: 'normal' },
  included: { type: Boolean, default: true },
  config: { type: Object, default: () => ({}) },
})

defineEmits(['update:value', 'update:included', 'browse'])

const isDisabled = computed(() => {
  if (props.disabled) return true
  if (props.showIncludeToggle && !props.included) return true
  if (props.field.disabledWhen && props.field.disabledWhen(props.config)) return true
  return false
})
</script>

<style scoped>
.field-indicator {
  margin-left: 0.25rem;
  font-size: 0.75rem;
  font-weight: bold;
  cursor: help;
  text-transform: none;
  letter-spacing: normal;
}

.field-indicator.autofilled { color: #856404; }
.field-indicator.modified { color: #cc5500; }
.field-indicator.profile { color: #0a6640; font-weight: bold; }
</style>
