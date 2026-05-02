<template>
  <div class="settings-sidebar-search">
    <div class="search-icon" aria-hidden="true">⌕</div>
    <input
      ref="inputRef"
      type="text"
      class="search-input"
      placeholder="Search settings…"
      :value="modelValue"
      @input="$emit('update:modelValue', $event.target.value)"
      @keydown.escape="$emit('update:modelValue', '')"
      @focus="settingsStore.setSidebarExpanded(true)"
      aria-label="Search settings"
    />
    <button
      v-if="modelValue"
      class="search-clear"
      @click="$emit('update:modelValue', '')"
      title="Clear search"
      aria-label="Clear search"
    >✕</button>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useSettingsStore } from '@/stores/settings'

defineProps({ modelValue: { type: String, default: '' } })
defineEmits(['update:modelValue'])

const settingsStore = useSettingsStore()

const inputRef = ref(null)

function focus() {
  inputRef.value?.focus()
}

defineExpose({ focus })
</script>

<style scoped>
.settings-sidebar-search {
  display: flex;
  align-items: center;
  margin: 8px 8px 4px;
  background: var(--bs-secondary-bg);
  border: 1px solid var(--bs-border-color);
  border-radius: 5px;
  overflow: hidden;
  transition: border-color 0.15s;
}

.settings-sidebar-search:focus-within {
  border-color: #6366f1;
}

.search-icon {
  padding: 0 6px 0 8px;
  color: var(--bs-tertiary-color);
  font-size: 14px;
  flex-shrink: 0;
}

.search-input {
  flex: 1;
  background: transparent;
  border: none;
  color: var(--bs-body-color);
  font-size: 12px;
  padding: 5px 4px;
  outline: none;
  min-width: 0;
}

.search-input::placeholder {
  color: var(--bs-tertiary-color);
}

.search-clear {
  background: none;
  border: none;
  color: var(--bs-tertiary-color);
  font-size: 11px;
  padding: 0 8px;
  cursor: pointer;
  flex-shrink: 0;
  height: 100%;
  display: flex;
  align-items: center;
}

.search-clear:hover {
  color: var(--bs-secondary-color);
}
</style>
