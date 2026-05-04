<template>
  <button
    class="settings-sidebar-item"
    :class="{ 'is-active': isActive, 'is-disabled': disabled, 'is-tinted': tinted }"
    :disabled="disabled"
    :title="label"
    @click="handleClick"
  >
    <span class="item-icon">{{ icon }}</span>
    <span class="item-label">{{ label }}</span>
  </button>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useSettingsStore } from '@/stores/settings'

const props = defineProps({
  to:       { type: String,  default: '' },
  icon:     { type: String,  required: true },
  label:    { type: String,  required: true },
  disabled: { type: Boolean, default: false },
  tinted:   { type: Boolean, default: false },
})

const route = useRoute()
const router = useRouter()
const settingsStore = useSettingsStore()

const isActive = computed(() => Boolean(props.to && route.path === props.to))

function handleClick() {
  if (props.disabled || !props.to) return
  settingsStore.setSidebarExpanded(false)
  router.push(props.to)
}
</script>

<style scoped>
.settings-sidebar-item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 8px 12px;
  background: none;
  border: none;
  border-left: 2px solid transparent;
  border-radius: 0 6px 6px 0;
  color: var(--bs-secondary-color);
  font-size: 13px;
  cursor: pointer;
  text-align: left;
  transition: background 0.12s, color 0.12s, border-color 0.12s;
  white-space: nowrap;
  overflow: hidden;
}

.settings-sidebar-item:hover:not(.is-disabled) {
  background: var(--bs-secondary-bg);
  color: var(--bs-emphasis-color);
}

.settings-sidebar-item.is-active {
  background: rgba(99, 102, 241, 0.15);
  color: #6366f1;
  border-left-color: #6366f1;
}

.settings-sidebar-item.is-tinted.is-active {
  background: var(--mode-tint);
  color: var(--mode-fg);
  border-left-color: var(--mode-fg);
}

.settings-sidebar-item.is-tinted:hover:not(.is-disabled):not(.is-active) {
  background: var(--mode-tint);
  color: var(--mode-fg);
}

.settings-sidebar-item.is-disabled {
  opacity: 0.4;
  cursor: default;
}

.item-icon {
  flex-shrink: 0;
  width: 20px;
  text-align: center;
  font-size: 14px;
}

.item-label {
  flex: 1;
}
</style>
