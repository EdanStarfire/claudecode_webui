<template>
  <div class="perm-queue-card" :style="{ borderLeftColor: perm.agentColor.border }">
    <div class="perm-card-header">
      <span class="perm-card-dot" :style="{ background: perm.agentColor.accent }"></span>
      <span class="perm-card-label">{{ perm.label }}</span>
    </div>
    <div class="perm-card-target">
      wants to use <code>{{ displayName }}</code>
    </div>
    <div class="perm-card-actions">
      <button
        type="button"
        class="perm-btn perm-btn-approve"
        :disabled="submitting"
        @click="$emit('approve')"
      >Approve</button>
      <button
        type="button"
        class="perm-btn perm-btn-deny"
        :disabled="submitting"
        @click="$emit('deny')"
      >Deny</button>
      <a href="#" class="perm-view-link" @click.prevent="$emit('view')">View in context</a>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

// Issue #1746 (stage: permissions): card markup shared between PermissionQueue.vue's desktop
// corner stack and mobile bottom sheet lists — kept as a plain presentational component (no
// store access of its own) so both lists render identical cards without duplicating markup.
const props = defineProps({
  perm: { type: Object, required: true },
  submitting: { type: Boolean, default: false }
})

defineEmits(['approve', 'deny', 'view'])

// Same field PermissionPrompt.vue reads (permission.display_name falling back to toolCall.name).
const displayName = computed(() => props.perm.toolCall.permission?.display_name || props.perm.toolCall.name)
</script>

<style scoped>
.perm-queue-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px 10px;
  border-left: 3px solid;
  border-radius: 6px;
  background: var(--bs-body-bg);
  font-size: 12px;
}

.perm-card-header {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.perm-card-dot {
  flex-shrink: 0;
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.perm-card-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 600;
  color: var(--bs-body-color);
}

.perm-card-target {
  color: var(--bs-secondary-color);
}

.perm-card-target code {
  font-size: 11px;
  background: var(--bs-tertiary-bg);
  padding: 1px 4px;
  border-radius: 3px;
}

.perm-card-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.perm-btn {
  padding: 3px 10px;
  border-radius: 4px;
  border: 1px solid transparent;
  font-size: 11px;
  font-weight: 500;
  cursor: pointer;
}
.perm-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.perm-btn-approve {
  background: #22c55e;
  border-color: #22c55e;
  color: white;
}
.perm-btn-approve:hover:not(:disabled) { background: #16a34a; }

.perm-btn-deny {
  background: #ef4444;
  border-color: #ef4444;
  color: white;
}
.perm-btn-deny:hover:not(:disabled) { background: #dc2626; }

.perm-view-link {
  font-size: 11px;
  color: var(--bs-link-color);
  margin-left: auto;
}
</style>
