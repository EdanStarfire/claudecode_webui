<template>
  <div class="settings-toolbar">
    <div class="toolbar-left">
      <h2 class="toolbar-title">{{ title }}</h2>
      <div v-if="chips && chips.length" class="toolbar-chips">
        <SettingsToolbarChip
          v-for="chip in chips"
          :key="chip.type + chip.label"
          :type="chip.type"
          :label="chip.label"
          :to="chip.to || ''"
        />
      </div>
    </div>
    <div v-if="showSaveCancel" class="toolbar-actions">
      <button class="btn-cancel" @click="$emit('cancel')" :disabled="saving">Cancel</button>
      <button class="btn-save" @click="$emit('save')" :disabled="saving">
        <span v-if="saving" class="save-spinner" aria-hidden="true">⟳</span>
        <span>{{ saving ? 'Saving…' : 'Save' }}</span>
      </button>
    </div>
  </div>
</template>

<script setup>
import SettingsToolbarChip from './SettingsToolbarChip.vue'

defineProps({
  title:         { type: String,  required: true },
  chips:         { type: Array,   default: () => [] },
  showSaveCancel:{ type: Boolean, default: false },
  saving:        { type: Boolean, default: false },
})

defineEmits(['save', 'cancel'])
</script>

<style scoped>
.settings-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 20px;
  border-bottom: 1px solid var(--mode-border, var(--bs-border-color));
  background: var(--mode-tint, var(--bs-tertiary-bg));
  flex-shrink: 0;
}

.toolbar-left {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  min-width: 0;
}

.toolbar-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--mode-fg, var(--bs-emphasis-color));
  white-space: nowrap;
}

.toolbar-chips {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.toolbar-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.btn-cancel,
.btn-save {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 5px 14px;
  border-radius: 6px;
  font-size: 13px;
  cursor: pointer;
  border: 1px solid;
  transition: background 0.12s, border-color 0.12s, color 0.12s;
}

.btn-cancel {
  background: none;
  border-color: var(--bs-border-color);
  color: var(--bs-secondary-color);
}

.btn-cancel:hover:not(:disabled) {
  background: var(--bs-secondary-bg);
  color: var(--bs-emphasis-color);
}

.btn-save {
  background: #6366f1;
  border-color: #6366f1;
  color: #fff;
}

.btn-save:hover:not(:disabled) {
  background: #818cf8;
  border-color: #818cf8;
}

.btn-cancel:disabled,
.btn-save:disabled {
  opacity: 0.5;
  cursor: default;
}

.save-spinner {
  display: inline-block;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}
</style>
