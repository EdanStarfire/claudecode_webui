<template>
  <div
    class="collapsible-panel"
    :class="{ 'collapsible-panel--expanded': expanded }"
    :style="panelStyle"
  >
    <!-- Header -->
    <button
      :id="`panel-header-${id}`"
      class="panel-header"
      :aria-expanded="expanded ? 'true' : 'false'"
      :aria-controls="`panel-body-${id}`"
      @click="$emit('update:expanded', !expanded)"
    >
      <svg
        class="panel-chevron"
        :class="{ 'panel-chevron-open': expanded }"
        width="12"
        height="12"
        viewBox="0 0 12 12"
        aria-hidden="true"
      >
        <path
          d="M4.5 2L8.5 6L4.5 10"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
          fill="none"
        />
      </svg>
      <span class="panel-title">{{ title }}</span>
      <span
        v-if="badge && badge > 0"
        class="badge bg-secondary panel-badge"
      >{{ badge }}</span>
      <div v-if="$slots['header-actions']" class="panel-header-actions" @click.stop>
        <slot name="header-actions" />
      </div>
    </button>

    <!-- Body -->
    <div
      v-show="expanded"
      :id="`panel-body-${id}`"
      role="region"
      :aria-labelledby="`panel-header-${id}`"
      class="panel-body"
    >
      <slot />
    </div>

    <!-- Inter-panel resize handle (rendered only when parent says a following panel is open) -->
    <div
      v-if="showResizeHandle"
      class="panel-resize-handle"
      @pointerdown.prevent="onResizePointerDown"
    />
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'

const props = defineProps({
  id: { type: String, required: true },
  title: { type: String, required: true },
  expanded: { type: Boolean, default: false },
  badge: { type: Number, default: null },
  flexWeight: { type: Number, default: 1 },
  showResizeHandle: { type: Boolean, default: false },
})

const emit = defineEmits(['update:expanded', 'resize-start', 'resize-move', 'resize-end'])

const panelStyle = computed(() => {
  if (!props.expanded) return { flex: '0 0 auto' }
  return { flex: `${props.flexWeight} 1 0` }
})

// Pointer-based resize handle (pointer capture keeps events even when cursor leaves)
let _lastY = 0

function onResizePointerDown(e) {
  const handle = e.currentTarget
  handle.setPointerCapture(e.pointerId)
  _lastY = e.clientY
  emit('resize-start', { clientY: e.clientY, panelId: props.id })

  handle.addEventListener('pointermove', onResizePointerMove)
  handle.addEventListener('pointerup', onResizePointerUp)
  handle.addEventListener('pointercancel', onResizePointerUp)
}

function onResizePointerMove(e) {
  const deltaY = e.clientY - _lastY
  _lastY = e.clientY
  emit('resize-move', { deltaY, panelId: props.id })
}

function onResizePointerUp(e) {
  const handle = e.currentTarget
  handle.releasePointerCapture(e.pointerId)
  handle.removeEventListener('pointermove', onResizePointerMove)
  handle.removeEventListener('pointerup', onResizePointerUp)
  handle.removeEventListener('pointercancel', onResizePointerUp)
  emit('resize-end', { panelId: props.id })
}
</script>

<style scoped>
.collapsible-panel {
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.collapsible-panel:not(.collapsible-panel--expanded) {
  min-height: 0;
}

.collapsible-panel--expanded {
  min-height: min-content;
}

.panel-header {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  width: 100%;
  border: none;
  border-bottom: 1px solid var(--bs-border-color);
  background: transparent;
  cursor: pointer;
  text-align: left;
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--bs-secondary-color);
  user-select: none;
  flex-shrink: 0;
  transition: background-color 0.15s ease;
}

.panel-header:hover {
  background-color: var(--bs-tertiary-bg);
  color: var(--bs-body-color);
}

.panel-chevron {
  flex-shrink: 0;
  color: var(--bs-secondary-color);
  transition: transform 0.15s ease;
}

.panel-chevron-open {
  transform: rotate(90deg);
}

.panel-title {
  flex-shrink: 0;
}

.panel-badge {
  font-size: 10px;
  padding: 1px 5px;
  line-height: 14px;
  min-width: 16px;
  flex-shrink: 0;
}

.panel-header-actions {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 4px;
}

.panel-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
}

.panel-resize-handle {
  flex-shrink: 0;
  height: 4px;
  cursor: row-resize;
  background-color: transparent;
  transition: background-color 0.15s ease;
  z-index: 1;
}

.panel-resize-handle:hover {
  background-color: var(--bs-border-color);
}
</style>
