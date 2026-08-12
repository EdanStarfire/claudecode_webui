<template>
  <div
    class="bc-session-card ghost"
    role="button"
    title="Open read-only archive view"
    :aria-label="`Open archive for deleted agent ${ghost.name}`"
    @click="handleClick"
  >
    <span class="chip-expand"></span>
    <span class="status-sq terminated"></span>
    <span class="chip-text">
      <span class="chip-name">{{ ghost.name || 'Unknown' }}</span>
      <span class="chip-sub">Deleted{{ ghost.archiveCount ? ` · ${ghost.archiveCount} archive${ghost.archiveCount > 1 ? 's' : ''}` : '' }}</span>
    </span>
    <button
      class="chip-dismiss"
      title="Remove from this list (does not delete the archive)"
      aria-label="Dismiss ghost agent"
      @click.stop="$emit('dismiss', agentId)"
    >&times;</button>
  </div>
</template>

<script setup>
defineProps({
  agentId: { type: String, required: true },
  ghost: { type: Object, required: true }
})

const emit = defineEmits(['select', 'dismiss'])

function handleClick() {
  emit('select')
}
</script>

<style scoped>
.bc-session-card {
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 7px 10px;
  margin-bottom: 4px;
  border-radius: 3px;
  border: 1px solid var(--bs-border-color);
  background: var(--bs-tertiary-bg);
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}

.bc-session-card.ghost {
  opacity: 0.6;
  border-style: dashed;
}

.bc-session-card.ghost:hover {
  opacity: 0.85;
  border-color: var(--bs-secondary-color);
}

.chip-expand {
  width: 14px;
  height: 14px;
  flex-shrink: 0;
}

.status-sq {
  width: 10px;
  height: 10px;
  border-radius: 2px;
  flex-shrink: 0;
}

.status-sq.terminated { background: #cbd5e1; }

.chip-text {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-width: 0;
}

.chip-name {
  font-size: 12.5px;
  font-weight: 500;
  color: var(--bs-emphasis-color);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chip-sub {
  font-size: 10.5px;
  color: var(--bs-secondary-color);
  font-style: italic;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chip-dismiss {
  flex-shrink: 0;
  width: 17px;
  height: 17px;
  border-radius: 50%;
  background: var(--bs-secondary-bg);
  border: 1px solid var(--bs-border-color);
  color: var(--bs-secondary-color);
  font-size: 12px;
  line-height: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  padding: 0;
}

.chip-dismiss:hover {
  background: #ef4444;
  border-color: #ef4444;
  color: #fff;
}
</style>
