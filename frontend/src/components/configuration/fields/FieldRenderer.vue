<template>
  <div class="field-row" :class="{ 'field-row--with-toggle': showIncludeToggle }">

    <!-- Include-toggle column (profile-manager context only) -->
    <div v-if="showIncludeToggle" class="field-toggle-col">
      <div class="form-check form-switch mb-0">
        <input
          class="form-check-input"
          type="checkbox"
          :checked="included"
          @change="$emit('update:included', $event.target.checked)"
        />
      </div>
    </div>

    <!-- Label column -->
    <div class="field-label-col" :class="{ 'field-disabled': showIncludeToggle && !included }">
      <template v-if="showBadges && fieldState">
        <!-- SourceMarker format: { kind: 'S'|'T'|'P'|'EMPTY', templateName?, profileName?, resettable? } -->
        <template v-if="typeof fieldState === 'object' && 'kind' in fieldState">
          <SourceMarker
            :kind="fieldState.kind"
            :template-name="fieldState.templateName || null"
            :profile-name="fieldState.profileName || null"
          />
          <button
            v-if="fieldState.resettable"
            type="button"
            class="field-reset-btn"
            title="Reset to inherited value"
            @click.stop="$emit('reset')"
          >↩</button>
        </template>
        <!-- Object fieldState: new 3-tier model (issue #1230) -->
        <template v-else-if="typeof fieldState === 'object'">
          <span
            v-if="fieldState.source && fieldState.source !== 'default'"
            class="field-indicator source-label"
            :class="fieldState.source"
            :title="`Value from ${fieldState.source_label}`"
          >{{ fieldState.source_label[0] }}</span>
          <button
            v-if="fieldState.is_set_here"
            type="button"
            class="field-reset-btn"
            title="Reset to inherited value"
            @click.stop="$emit('reset')"
          >&#x21BA;</button>
        </template>
        <!-- String fieldState: legacy profile-editor mode -->
        <template v-else>
          <span v-if="fieldState === 'profile'" class="field-indicator profile" title="Value from profile">P</span>
          <span v-if="fieldState === 'autofilled'" class="field-indicator autofilled">&lt;</span>
          <span v-if="fieldState === 'modified'" class="field-indicator modified">*</span>
        </template>
      </template>
      <span class="field-label-text">{{ field.label }}</span>
    </div>

    <!-- Control column -->
    <div class="field-control-col" :class="{ 'field-disabled': showIncludeToggle && !included }">
      <ButtonGroupWidget
        v-if="field.widget === 'button-group'"
        :value="value"
        :disabled="isDisabled"
        :options="field.options || []"
        :multiple="field.multiple || false"
        :default-value="field.defaultValue !== undefined ? field.defaultValue : null"
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
        :default-value="field.defaultValue !== undefined ? field.defaultValue : null"
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

      <TagListField
        v-else-if="field.widget === 'tag-list'"
        :value="value"
        :disabled="isDisabled"
        :placeholder="field.placeholder || 'Add...'"
        @update:value="$emit('update:value', $event)"
      />

      <MultiSelectField
        v-else-if="field.widget === 'multi-select'"
        :value="value"
        :disabled="isDisabled"
        :options-from="field.optionsFrom || null"
        :placeholder="field.placeholder || 'Select...'"
        @update:value="$emit('update:value', $event)"
      />

      <small v-if="field.description" class="field-description">
        <span v-html="field.description" />
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
import TagListField from './TagListField.vue'
import MultiSelectField from './MultiSelectField.vue'
import SourceMarker from '../../settings/SourceMarker.vue'

const props = defineProps({
  field: { type: Object, required: true },
  value: { type: [String, Number, Boolean, Array, Object], default: undefined },
  disabled: { type: Boolean, default: false },
  showBadges: { type: Boolean, default: false },
  showIncludeToggle: { type: Boolean, default: false },
  // String (legacy profile-editor) or Object {source, source_label, is_set_here} (issue #1230)
  fieldState: { type: [String, Object], default: 'normal' },
  included: { type: Boolean, default: true },
  config: { type: Object, default: () => ({}) },
})

defineEmits(['update:value', 'update:included', 'browse', 'reset'])

const isDisabled = computed(() => {
  if (props.disabled) return true
  if (props.showIncludeToggle && !props.included) return true
  if (props.field.disabledWhen && props.field.disabledWhen(props.config)) return true
  return false
})
</script>

<style scoped>
/* Desktop: label (220px) | control (1fr) */
.field-row {
  display: grid;
  grid-template-columns: 220px 1fr;
  gap: 12px;
  align-items: start;
  padding: 10px 0;
  border-bottom: 1px solid var(--bs-border-color);
}

/* With include-toggle: toggle | label | control */
.field-row--with-toggle {
  grid-template-columns: auto 220px 1fr;
}

.field-toggle-col {
  padding-top: 5px;
}

.field-label-col {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  padding-top: 5px;
  min-width: 0;
}

.field-label-text {
  font-size: 13px;
  font-weight: 500;
  color: var(--bs-emphasis-color);
  line-height: 1.4;
}

.field-control-col {
  min-width: 0;
}

.field-disabled {
  opacity: 0.45;
  pointer-events: none;
}

.field-description {
  display: block;
  margin-top: 5px;
  font-size: 11px;
  color: var(--bs-secondary-color);
  line-height: 1.5;
}

/* Mobile: stack label above control */
@container settings-area (max-width: 599px) {
  .field-row,
  .field-row--with-toggle {
    grid-template-columns: 1fr;
  }

  .field-toggle-col {
    padding-top: 0;
  }

  .field-label-col {
    padding-top: 0;
  }
}

/* Legacy indicator styles (non-SourceMarker paths) */
.field-indicator {
  font-size: 0.75rem;
  font-weight: bold;
  cursor: help;
}

.field-indicator.autofilled { color: #856404; }
.field-indicator.modified   { color: #cc5500; }
.field-indicator.profile    { color: #0a6640; font-weight: bold; }
.field-indicator.source-label          { color: #6c757d; }
.field-indicator.source-label.template { color: #0d6efd; }
.field-indicator.source-label.profile  { color: #0a6640; }
.field-indicator.source-label.session  { color: #cc5500; }

.field-reset-btn {
  background: none;
  border: none;
  padding: 0;
  font-size: 0.85rem;
  color: #6c757d;
  cursor: pointer;
  line-height: 1;
}
.field-reset-btn:hover { color: #dc3545; }
</style>
